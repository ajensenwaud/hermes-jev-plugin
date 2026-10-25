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

- `jev_model` — TypeSafe model id (default `jev-latest`). Hermes reserves the
  `model` settings key for core, so the plugin reads `jev_model` instead.
  Pin a versioned id (e.g. `jev-1.14.0`) if you tuned thresholds against it.
- `timeout_seconds` — HTTP timeout (default 30)
- `max_questions` — max questions per `jev_evaluate` call (default 20)

Each setting is read on its own; an invalid value (including a non-positive
`timeout_seconds` or `max_questions`) falls back to its default without
discarding the others.

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

## Testing

Offline unit tests (no API key, no network):

```
python -m unittest discover -s tests -p "test_*.py"
```

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
pyproject.toml # python dependencies (httpx)
__init__.py    # register(ctx): wires schemas → handlers, bundles the skill
schemas.py     # JSON schemas the LLM reads to decide when to call
tools.py       # handlers: always return JSON strings, errors included
client.py      # thin httpx wrapper around the TypeSafe System One API
skills/        # bundled usage skill (jev-questions)
tests/         # agent-path integration harness
```

## License

MIT — see [LICENSE](LICENSE).
