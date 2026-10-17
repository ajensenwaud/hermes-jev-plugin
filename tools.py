"""Tool handlers — the code that runs when the LLM calls each tool.

Handler rules (per Hermes plugin contract):
1. Receive args (dict) — the parameters the model passed
2. Do the work
3. Return a JSON string — ALWAYS, even on error
4. Accept **kwargs for forward compatibility
"""

from __future__ import annotations

import json
import os
from typing import Any

from . import client, images as images_mod


def _config(ctx: Any) -> dict:
    """Read plugin settings with defaults; never raise."""
    cfg = {
        "model": client.DEFAULT_MODEL,
        "timeout_seconds": client.DEFAULT_TIMEOUT,
        "vision_enabled": False,
        "vision_model": "jev-preview",
    }
    try:
        if ctx is not None:
            cfg["model"] = ctx.get_config("model", default=client.DEFAULT_MODEL)
            cfg["timeout_seconds"] = int(ctx.get_config("timeout_seconds", default=client.DEFAULT_TIMEOUT))
            cfg["vision_enabled"] = bool(ctx.get_config("vision_enabled", default=False))
            cfg["vision_model"] = str(ctx.get_config("vision_model", default="jev-preview"))
    except Exception:
        pass
    return cfg


def _error(msg: str, **extra: Any) -> str:
    return json.dumps({"error": msg, **extra}, ensure_ascii=False)


def evaluate(args: dict, ctx=None, **kwargs) -> str:
    state = args.get("state")
    if not isinstance(state, str) or not state.strip():
        return _error("Need a non-empty 'state' string.")
    if len(state) > 200_000:
        state = state[:200_000]

    try:
        questions = client.normalize_questions(args.get("questions"))
    except client.JevError as e:
        return _error(str(e))

    cfg = _config(ctx)
    max_q = cfg.get("max_questions", 20)
    if len(questions) > max_q:
        return _error(f"Too many questions ({len(questions)}); max is {max_q} per call.")

    model = args.get("model") or cfg["model"]
    try:
        resp = client.call_system_one(
            state=state,
            questions=questions,
            model=model,
            timeout=float(cfg["timeout_seconds"]),
        )
    except client.JevError as e:
        return _error(str(e))

    answers = resp.get("answers")
    if not isinstance(answers, dict):
        return _error("TypeSafe returned no answers object.", raw=resp)

    out: dict[str, Any] = {
        "model": client.model_of(resp) or model,
        "answers": client.summarize_answers(answers),
    }
    usage = client.usage_of(resp)
    if usage:
        out["usage"] = usage
    return client.compact_json(out)


def check(args: dict, ctx=None, **kwargs) -> str:
    state = args.get("state")
    question = args.get("question")
    if not isinstance(state, str) or not state.strip():
        return _error("Need a non-empty 'state' string.")
    if not isinstance(question, str) or not question.strip():
        return _error("Need a non-empty 'question' string.")
    if len(state) > 200_000:
        state = state[:200_000]

    cfg = _config(ctx)
    q = {
        "jev_check": {
            "type": "noul",
            "instructions": question,
        }
    }
    criterion = args.get("criterion")
    if isinstance(criterion, dict) and criterion:
        q["jev_check"]["criterion"] = criterion

    model = args.get("model") or cfg["model"]
    try:
        resp = client.call_system_one(
            state=state, questions=q, model=model, timeout=float(cfg["timeout_seconds"])
        )
    except client.JevError as e:
        return _error(str(e))

    ans = (resp.get("answers") or {}).get("jev_check", {})
    p_yes = ans.get("noul")
    if p_yes is None:
        return _error("TypeSafe returned no noul value.", raw=resp)

    # Interpretation guidance for the caller.
    verdict = "yes" if p_yes >= 0.75 else "no" if p_yes <= 0.25 else "uncertain"
    return client.compact_json(
        {
            "model": client.model_of(resp) or model,
            "question": question,
            "yes_probability": p_yes,
            "verdict": verdict,
            "usage": client.usage_of(resp),
        }
    )


def route(args: dict, ctx=None, **kwargs) -> str:
    state = args.get("state")
    question = args.get("question")
    options = args.get("options")
    if not isinstance(state, str) or not state.strip():
        return _error("Need a non-empty 'state' string.")
    if not isinstance(question, str) or not question.strip():
        return _error("Need a non-empty 'question' string.")
    if not isinstance(options, dict) or len(options) < 2:
        return _error("'options' must be an object with at least 2 entries: {key: description}.")
    if len(state) > 200_000:
        state = state[:200_000]

    cfg = _config(ctx)
    q = {
        "jev_route": {
            "type": "choice",
            "instructions": question,
            "criteria": {str(k): str(v) for k, v in options.items()},
        }
    }
    model = args.get("model") or cfg["model"]
    try:
        resp = client.call_system_one(
            state=state, questions=q, model=model, timeout=float(cfg["timeout_seconds"])
        )
    except client.JevError as e:
        return _error(str(e))

    ans = (resp.get("answers") or {}).get("jev_route", {})
    choice = ans.get("choice")
    if not choice:
        return _error("TypeSafe returned no choice.", raw=resp)

    probs = ans.get("probabilities") or {}
    confidence = ans.get("confidence")
    return client.compact_json(
        {
            "model": client.model_of(resp) or model,
            "question": question,
            "choice": choice,
            "probabilities": probs,
            "confidence": confidence,
            "usage": client.usage_of(resp),
        }
    )


def score(args: dict, ctx=None, **kwargs) -> str:
    state = args.get("state")
    question = args.get("question")
    levels = args.get("levels")
    if not isinstance(state, str) or not state.strip():
        return _error("Need a non-empty 'state' string.")
    if not isinstance(question, str) or not question.strip():
        return _error("Need a non-empty 'question' string.")
    if not isinstance(levels, list) or len(levels) < 2:
        return _error("'levels' must be a list of at least 2 ordered descriptions, lowest first.")
    if len(state) > 200_000:
        state = state[:200_000]

    cfg = _config(ctx)
    q = {
        "jev_score": {
            "type": "score",
            "instructions": question,
            "criteria": [str(l) for l in levels],
        }
    }
    model = args.get("model") or cfg["model"]
    try:
        resp = client.call_system_one(
            state=state, questions=q, model=model, timeout=float(cfg["timeout_seconds"])
        )
    except client.JevError as e:
        return _error(str(e))

    ans = (resp.get("answers") or {}).get("jev_score", {})
    value = ans.get("score")
    if value is None:
        return _error("TypeSafe returned no score.", raw=resp)

    return client.compact_json(
        {
            "model": client.model_of(resp) or model,
            "question": question,
            "score": value,
            "legend": ans.get("legend"),
            "confidence": ans.get("confidence"),
            "usage": client.usage_of(resp),
        }
    )


def find(args: dict, ctx=None, **kwargs) -> str:
    """jev_find: visibility-gated Box (Jev 1.14 vision).

    Two questions in ONE request: a presence Noul (the gate) and the Box.
    The gate exists because a Box answer is ALWAYS a box, even when the
    object is absent — confident-looking full-image boxes on empty photos.
    This tool always returns the raw box so nothing is hidden from callers;
    the skip decision is the caller's, via gate_passed/skipped_by_gate.
    """
    image_path = args.get("image_path")
    image_url = args.get("image_url")
    target = args.get("target")
    if not isinstance(target, str) or not target.strip():
        return _error("Need a non-empty 'target' describing what to find in the image.")

    try:
        gate_floor = float(args.get("gate_floor", 0.6))
    except (TypeError, ValueError):
        return _error("gate_floor must be a number from 0 to 1.")
    if not 0.0 <= gate_floor <= 1.0:
        return _error("gate_floor must be a number from 0 to 1.")

    if image_path and image_url:
        return _error("Pass image_path or image_url, not both.")
    if image_url and not image_path:
        temp_path = images_mod.download_to_temp(str(image_url))
        image_path = temp_path
    if not isinstance(image_path, str) or not image_path.strip():
        return _error("Need image_path (a local PNG/JPEG file) or image_url.")
    image_path = os.path.abspath(os.path.expanduser(image_path))
    if not os.path.isfile(image_path):
        return _error(f"image_path does not exist: {image_path}")

    cfg = _config(ctx)
    model = args.get("model") or cfg["vision_model"]

    try:
        ref, asset = images_mod.local_image_to_asset(image_path)
    except client.JevError as e:
        return _error(str(e))
    ref_key = f"{ref[:8]}-photo"
    assets = {ref_key: asset}
    try:
        state = images_mod.make_state(ref_key, args.get("state"))
        questions = {
            "present": {
                "type": "noul",
                "instructions": f"Is {target} visible in `{ref_key}`?",
            },
            "locate": {
                "type": "box",
                "instructions": f"{target} in `{ref_key}`",
            },
        }
        resp = client.call_system_one(
            state=state,
            questions=questions,
            model=model,
            timeout=float(cfg["timeout_seconds"]),
            assets=assets,
        )
    except client.JevError as e:
        return _error(str(e))

    answers = resp.get("answers")
    if not isinstance(answers, dict):
        return _error("TypeSafe returned no answers object.", raw=resp)
    present = (answers.get("present") or {}).get("noul")
    locate = answers.get("locate") or {}
    if present is None and locate.get("type") != "box":
        return _error("TypeSafe returned neither the gate noul nor a box answer.", raw=resp)

    gate_passed = (present is not None) and present >= gate_floor
    out: dict[str, Any] = {
        "model": client.model_of(resp) or model,
        "target": target,
        "image": image_path,
        "gate_floor": gate_floor,
        "present": present,
        "gate_passed": gate_passed,
        "skipped_by_gate": not gate_passed,
        "box": locate.get("box"),
        "box_confidence": locate.get("confidence"),
        "usage": client.usage_of(resp),
    }
    return client.compact_json(out)
