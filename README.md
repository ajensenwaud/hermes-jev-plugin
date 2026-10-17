# Jev plugin for Hermes Agent

[TypeSafe Jev](https://docs.typesafe.ai/introduction) is a "System One" decision
model: it evaluates typed questions (Noul / Choice / Score) against a state and
returns structured answers with calibrated probabilities. This plugin exposes it
to [Hermes Agent](https://github.com/nousresearch/hermes-agent) as four tools,
focused on coding-agent workflows.

## Tools

| Tool | Question type | Use for |
|------|---------------|---------|
| `jev_evaluate` | any mix, one call | several judgments on the same state (parallel, cheap) |
| `jev_check` | Noul (yes/no) | atomic binary judgments: "is this failure transient?" |
| `jev_route` | Choice | pick one option: which subsystem owns a bug, which agent handles a task |
| `jev_score` | Score | ordered rubric rating: severity, complexity, risk |

The bundled skill `jev:jev-questions` (load with `skill_view("jev:jev-questions")`)
covers atomic question design, state preparation, and confidence gating.

## Install

```
hermes plugins install ajensenwaud/hermes-jev-plugin
```

(Or clone the repo into `~/.hermes/plugins/jev/` and run `hermes plugins enable jev`.)

You need a TypeSafe API key (https://console.typesafe.ai/keys). The installer
prompts for `TYPESAFE_API_KEY` (saved to `~/.hermes/.env`), or set it yourself.

## Settings (`plugins.entries.jev.settings` in config.yaml)

- `model` — TypeSafe model id (default `jev-latest`)
- `timeout_seconds` — HTTP timeout (default 30)
- `max_questions` — max questions per `jev_evaluate` call (default 20)

## Coding-agent recipes

Failure triage (state = raw error):
```json
{"transient": {"type": "noul", "instructions": "Is this failure transient (retrying could succeed)?"},
 "missing_dep": {"type": "noul", "instructions": "Is this a missing dependency or version conflict?"}}
```
transient → retry; missing_dep → fix versions; else escalate.

Review gate (state = diff):
```json
{"blocks_merge": {"type": "noul", "instructions": "Would a senior engineer block this diff as-is?"},
 "security_risk": {"type": "noul", "instructions": "Does this diff introduce a security risk?"}}
```

Change routing (state = task description): `jev_route` with options
`frontend / backend / infra / unknown`.

Severity (state = bug report): `jev_score` with levels
`["Cosmetic", "Minor", "Major", "Blocks release"]`.

Note: score questions accept the `levels` key on the tool and are mapped to the
API's `criteria` list automatically; `jev_evaluate` callers may use either
spelling (`criteria` wins if both are present).

## Vision (Jev 1.14, `jev_find`)

Jev 1.14.0 (alias `jev-preview`, released 2026-10-14) adds image input and the
Box question type: where something is in a photo. TypeSafe calls this
"Dreamer-ready" — the request format, answers, and limits can change between
releases, so vision ships **behind a setting**:

```yaml
plugins:
  entries:
    jev:
      settings:
        vision_enabled: true    # off by default
        vision_model: jev-preview
```

`jev_find(image_path|image_url, target)` sends ONE request with two questions:

1. **Presence gate (a Noul)** — "is the target visible?"
2. **Box** — `{top, bottom, left, right}`, each a 0-1 fraction of image
   width/height, plus `confidence`.

The gate exists because a Box ALWAYS answers, even on an empty photo (an
independent 1,000-photo test observed 20 empty photos all returning boxes).
Read `gate_passed`/`skipped_by_gate` first; only trust `box` after the gate
passes. Both confidences jitter run-to-run (observed ≈0.05 or worse on
presence nouls across identical inputs) — treat a weak gate as a retry
signal, not proof of absence. Small targets in large images get missed: crop
the region and ask again, or ask about regions first.

Preparation is built in: PNG/JPEG (converted), EXIF-rotated upright (the API
strips metadata and never applies EXIF orientation), longest side ≤ 1568 px,
each side ≥ 280 px (padded up), baseline JPEG re-encode. Constraints worth
knowing: 16 MiB per file; 32 MiB request body; every image must be referenced;
a Choice question without `criteria` is HTTP 422.

Out of scope and why: browser/computer-use driving is a different project; a
tile-grid search for multi-megapixel images needs more real-image validation
before shipping.

## Testing

`tests/agent_test.py` is a stdlib-only integration harness that drives real
`hermes -z` sessions (one per scenario, restricted to the `jev` toolset) and
asserts on the JSON each tool returns through the full agent path:

1. clear-cut noul → confident yes (p ≥ 0.70)
2. calibration noul → no extreme confidence on an uninformative state
3. route → picks the owning subsystem, with probabilities
4. mixed evaluate (noul + choice + score in one call, using the `levels` alias)
5. malformed call → graceful error JSON, hermes exit code 0

Run it with any python3 after installing and enabling the plugin with a valid
`TYPESAFE_API_KEY`:

```
python3 tests/agent_test.py
```

Probabilities drift slightly run-to-run (±0.02 observed), so assertions use
tolerance bands rather than exact values.

## Layout

```
plugin.yaml    # Hermes manifest (v1): tools, env, config schema
pyproject.toml # python dependencies (httpx, pillow)
__init__.py    # register(ctx): wires schemas → handlers, bundles the skill
schemas.py     # JSON schemas the LLM reads to decide when to call
tools.py       # handlers: always return JSON strings, errors included
images.py      # EXIF-safe image preparation for image-capable tools
client.py      # thin httpx wrapper around the TypeSafe System One API
skills/        # bundled usage skill (jev-questions)
tests/         # agent-path integration harness
```

## License

MIT — see [LICENSE](LICENSE).
