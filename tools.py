"""Tool handlers — the code that runs when the LLM calls each tool.

Handler rules (per Hermes plugin contract):
1. Receive args (dict) — the parameters the model passed
2. Do the work
3. Return a JSON string — ALWAYS, even on error
4. Accept **kwargs for forward compatibility
"""

from __future__ import annotations

import json
from typing import Any

from . import client


DEFAULT_MAX_QUESTIONS = 20

# Hermes reserves the plugin-settings roots `model`, `plugins`, `security` and
# `settings`: ctx.get_config("model") raises. The model id is therefore read
# from `jev_model`.
def _positive(cast):
    def parse(value):
        number = cast(value)
        if number <= 0:
            raise ValueError(f"expected a positive number, got {value!r}")
        return number
    return parse


_SETTINGS = (
    # (config key, cfg key, parser, default). A value the parser rejects falls back
    # to its default, so a non-positive limit or timeout cannot disable the tool.
    ("jev_model", "model", str, client.DEFAULT_MODEL),
    ("timeout_seconds", "timeout_seconds", _positive(float), client.DEFAULT_TIMEOUT),
    ("max_questions", "max_questions", _positive(int), DEFAULT_MAX_QUESTIONS),
)


def _config(ctx: Any) -> dict:
    """Read plugin settings with defaults; never raise.

    Each key is read and cast on its own, so one bad value falls back to its
    default without discarding the other settings.
    """
    cfg = {name: default for _, name, _, default in _SETTINGS}
    if ctx is None:
        return cfg
    for key, name, parse, default in _SETTINGS:
        try:
            value = ctx.get_config(key, default=default)
            cfg[name] = parse(value) if value not in (None, "") else default
        except Exception:
            cfg[name] = default
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
    max_q = cfg["max_questions"]
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
