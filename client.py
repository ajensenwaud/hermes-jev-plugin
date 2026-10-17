"""TypeSafe Jev System One API client.

Thin httpx wrapper around POST https://api.typesafe.ai/v1/systemone.
No vendor SDK dependency: the request/response surface is small and stable
(state + typed questions -> typed answers with probabilities/confidence).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT = 30.0

VALID_TYPES = {"noul", "choice", "score"}
# "box" is valid only with the images header (see IMAGES_HEADER) and only on
# Jev 1.14 (alias jev-preview). Text-only tools never send it.
BOX_TYPE = "box"
IMAGES_HEADER = "TypeSafe-Danger-Zone-Images"
# Docs limits: PNG/JPEG, longest side <= 1568, each side >= 280, 16 MiB/file.
MAX_IMAGE_SIDE = 1568
MIN_IMAGE_SIDE = 280


class JevError(Exception):
    """Raised when the TypeSafe API call fails or the request is malformed."""


def _get_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        raise JevError(
            "TYPESAFE_API_KEY is not set. Get one at https://console.typesafe.ai/keys "
            "and add it to ~/.hermes/.env (or the environment Hermes runs in)."
        )
    return key


def normalize_questions(raw: Any) -> dict:
    """Accept dict-form questions (name -> spec) and return a validated dict.

    Each spec must be a dict with:
      type: 'noul' | 'choice' | 'score' | 'box'
      instructions: str (the atomic question; required for all types)
      criteria: dict (choice) | list[dict] (score) | dict (noul, optional)
      box questions take NO criteria.

    Also tolerates the SDK-style 'criterion' key used by noul questions.
    """
    if not isinstance(raw, dict) or not raw:
        raise JevError(
            "questions must be a non-empty JSON object mapping name -> "
            "{'type': 'noul'|'choice'|'score'|'box', 'instructions': str, 'criteria': ...}"
        )

    out: dict[str, dict] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not name or len(name) > 64:
            raise JevError(f"invalid question name: {name!r}")
        if not isinstance(spec, dict):
            raise JevError(f"question '{name}' must be an object")
        qtype = spec.get("type")
        if qtype not in VALID_TYPES | {BOX_TYPE}:
            raise JevError(f"question '{name}': type must be one of {sorted(VALID_TYPES | {BOX_TYPE})}")
        instr = spec.get("instructions")
        if not isinstance(instr, str) or not instr.strip():
            raise JevError(f"question '{name}': missing 'instructions'")

        q: dict[str, Any] = {"type": qtype, "instructions": instr}

        if qtype == BOX_TYPE:
            if spec.get("criteria") is not None:
                raise JevError(f"question '{name}': box questions take no criteria")
        elif qtype == "choice":
            criteria = spec.get("criteria")
            if not isinstance(criteria, dict) or not criteria:
                raise JevError(
                    f"question '{name}': choice needs criteria as an object "
                    "{'option_key': 'description'}"
                )
            if len(criteria) < 2:
                raise JevError(f"question '{name}': choice needs at least 2 options")
            q["criteria"] = criteria
        elif qtype == "score":
            criteria = spec.get("criteria")
            if criteria is None:
                criteria = spec.get("levels")  # tolerate jev_score-style 'levels' key
            if not isinstance(criteria, list) or not criteria:
                raise JevError(
                    f"question '{name}': score needs criteria (or levels) as a list of "
                    "level descriptions, lowest first"
                )
            q["criteria"] = criteria
        else:  # noul
            criterion = spec.get("criterion", spec.get("criteria"))
            if criterion is not None:
                if isinstance(criterion, dict) and not criterion:
                    criterion = None
                else:
                    q["criterion"] = criterion
        out[name] = q
    return out


def build_payload(
    state: Any,
    questions: dict,
    model: str = DEFAULT_MODEL,
    assets: dict | None = None,
) -> tuple[dict, dict[str, str]]:
    """Build the request payload + headers; validate assets before sending.

    Asset validation runs here so malformed image fields fail locally,
    before any network round trip (docs: unreferenced/broken assets make
    the WHOLE request fail with 400).
    """
    payload: dict[str, Any] = {"state": state, "model": model, "questions": questions}
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if assets:
        for ref, entry in assets.items():
            if not isinstance(ref, str) or not ref or len(ref) > 1024:
                raise JevError(f"invalid image reference: {ref!r}")
            if not isinstance(entry, dict) or entry.get("type") != "image":
                raise JevError(f"asset {ref!r}: entries must be type 'image'")
            if not entry.get("data_base64"):
                raise JevError(f"asset {ref!r}: missing data_base64")
            blob = json.dumps(payload)
            if ref not in blob:
                raise JevError(
                    f"asset {ref!r} is not referenced anywhere in state/questions; "
                    "the API rejects unreferenced assets with HTTP 400"
                )
        payload["assets"] = assets
        headers[IMAGES_HEADER] = "true"
    return payload, headers


def call_system_one(
    state: Any,
    questions: dict,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = 2,
    assets: dict | None = None,
) -> dict:
    """POST state + questions to TypeSafe; return the parsed response dict.

    `state` may be a string, a JSON object, or a JSON array. `assets` maps an
    image reference string -> {'type': 'image', 'format': 'png'|'jpg',
    'data_base64': ...}. Sending assets adds the TypeSafe-Danger-Zone-Images
    header; a request with an image or a box question without it is HTTP 400.
    """
    key = _get_key()
    payload, headers = build_payload(state, questions, model, assets)
    headers["Authorization"] = f"Bearer {key}"

    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(timeout=timeout) as http:
                resp = http.post(API_URL, headers=headers, json=payload)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                wait = 1.5 * (attempt + 1)
                logger.warning("TypeSafe API %s; retrying in %.1fs", resp.status_code, wait)
                time.sleep(wait)
                continue
            if resp.status_code == 401:
                raise JevError("TypeSafe rejected the API key (401). Check TYPESAFE_API_KEY.")
            if resp.status_code == 402:
                raise JevError("TypeSafe account issue (402): credits or plan. Check the console.")
            if resp.status_code == 422:
                try:
                    detail = resp.json()
                except Exception:
                    detail = resp.text[:500]
                raise JevError(f"TypeSafe rejected the request (422): {detail}")
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise JevError(f"TypeSafe API timed out after {timeout}s: {e}") from e
        except httpx.HTTPStatusError as e:
            raise JevError(f"TypeSafe API error {e.response.status_code}: {e.response.text[:300]}") from e
        except httpx.HTTPError as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise JevError(f"TypeSafe API unreachable: {e}") from e

    raise JevError(f"TypeSafe API failed after retries: {last_err}")


def summarize_answers(answers: dict) -> dict:
    """Flatten the answers object into a compact, tool-friendly summary."""
    out: dict[str, Any] = {}
    for name, ans in answers.items():
        if not isinstance(ans, dict):
            continue
        qtype = ans.get("type")
        if qtype == "noul":
            entry: dict[str, Any] = {"type": "noul", "yes_probability": ans.get("noul")}
        elif qtype == "choice":
            entry = {
                "type": "choice",
                "choice": ans.get("choice"),
                "probabilities": ans.get("probabilities"),
                "confidence": ans.get("confidence"),
            }
        elif qtype == "score":
            entry = {
                "type": "score",
                "score": ans.get("score"),
                "legend": ans.get("legend"),
                "confidence": ans.get("confidence"),
            }
        elif qtype == BOX_TYPE:
            entry = {
                "type": "box",
                "box": ans.get("box"),
                "confidence": ans.get("confidence"),
            }
        else:
            entry = {"type": qtype, "raw": ans}
        out[name] = entry
    return out


def usage_of(resp: dict) -> dict | None:
    u = resp.get("usage")
    return u if isinstance(u, dict) else None


def model_of(resp: dict) -> str | None:
    m = resp.get("model")
    return m if isinstance(m, str) else None


def compact_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
