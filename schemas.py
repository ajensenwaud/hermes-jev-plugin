"""Tool schemas — what the LLM sees."""

EVALUATE = {
    "name": "jev_evaluate",
    "description": (
        "Ask TypeSafe Jev (a 'System One' decision model, NOT a text model) one or "
        "more typed questions against a state, and get structured answers with "
        "calibrated probabilities. Question types: 'noul' (yes/no -> yes_probability), "
        "'choice' (pick one option -> choice + probabilities), 'score' (rate on an "
        "ordered rubric -> numeric score + legend). All questions are evaluated in "
        "parallel against the same state in ONE call; asking several is cheap. Keep "
        "each question atomic (a gut-check judgment, not multi-step reasoning) — "
        "decompose complex judgments into several atomic questions and combine the "
        "answers yourself. Use for triage, routing, failure classification, review "
        "gating, and go/no-go checks."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "description": "The context to evaluate: pasted text, error output, "
                "diff summary, code snippet, or a compact description of the situation.",
            },
            "questions": {
                "type": "object",
                "description": "Map of question_name -> spec. Spec fields: type "
                "('noul'|'choice'|'score'), instructions (the atomic question), and "
                "type-specific criteria. Examples: "
                '{"transient":{"type":"noul","instructions":"Is this failure transient '
                '(retrying could help)?"}} ; '
                '{"team":{"type":"choice","instructions":"Which team should own this",'
                '"criteria":{"backend":"...","frontend":"...","infra":"..."}}} ; '
                '{"severity":{"type":"score","instructions":"How severe is this bug",'
                '"criteria":["Cosmetic","Minor","Major","Blocks release"]}}',
            },
            "model": {
                "type": "string",
                "description": "Optional TypeSafe model id (default jev-latest).",
            },
        },
        "required": ["state", "questions"],
    },
}

CHECK = {
    "name": "jev_check",
    "description": (
        "Ask Jev a single yes/no (noul) question about a state and get the "
        "probability that the answer is yes (0-1). Use for atomic binary judgments "
        "about code, errors, logs, or plans: 'Is this stack trace a missing-dependency "
        "error?', 'Is this failure transient (a retry could fix it)?', 'Does this diff "
        "touch authentication logic?', 'Is this migration backwards-compatible?'. One "
        "question per call — make several calls (or use jev_evaluate) for multiple "
        "judgments."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "description": "The context to evaluate.",
            },
            "question": {
                "type": "string",
                "description": "A single, atomic yes/no question.",
            },
            "criterion": {
                "type": "object",
                "description": "Optional structured criteria the model should check "
                "(JSON object of named requirements).",
            },
            "model": {
                "type": "string",
                "description": "Optional TypeSafe model id (default jev-latest).",
            },
        },
        "required": ["state", "question"],
    },
}

ROUTE = {
    "name": "jev_route",
    "description": (
        "Ask Jev to choose exactly one option from a defined set for a given state "
        "(a Choice question). Use for routing decisions: which subsystem a bug "
        "belongs to, which agent should handle a task, which fix strategy to apply, "
        "which test suite to run first. Returns the chosen option, per-option "
        "probabilities, and confidence. Options must be mutually exclusive and "
        "exhaustive enough to cover the input."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "description": "The context to evaluate.",
            },
            "question": {
                "type": "string",
                "description": "What to choose, e.g. 'Which component owns this failure?'",
            },
            "options": {
                "type": "object",
                "description": "Map of option_key -> short description. Example: "
                '{"auth":"login/session issues","db":"queries or migrations",'
                '"network":"timeouts and connectivity"}',
            },
            "model": {
                "type": "string",
                "description": "Optional TypeSafe model id (default jev-latest).",
            },
        },
        "required": ["state", "question", "options"],
    },
}

SCORE = {
    "name": "jev_score",
    "description": (
        "Ask Jev to rate a state against an ordered rubric (a Score question) and "
        "get a numeric score with per-level legend and confidence. Use for "
        "graduated judgments: severity of a review finding, code-review blocking-ness, "
        "task complexity, riskiness of a migration, confidence in a plan. Levels are "
        "ordered lowest to highest; 3-5 concrete levels work best."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "description": "The context to evaluate.",
            },
            "question": {
                "type": "string",
                "description": "What to score, e.g. 'How severe is this review finding?'",
            },
            "levels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ordered level descriptions, lowest first. Example: "
                '["Cosmetic","Minor","Major","Blocks release"]',
            },
            "model": {
                "type": "string",
                "description": "Optional TypeSafe model id (default jev-latest).",
            },
        },
        "required": ["state", "question", "levels"],
    },
}

SCHEMAS = [EVALUATE, CHECK, ROUTE, SCORE]

FIND = {
    "name": "jev_find",
    "description": (
        "Find an object in an IMAGE and get its bounding box, protected by a "
        "presence gate. Vision requires Jev 1.14 (the plugin's vision_model "
        "default 'jev-preview'); TypeSafe marks images/Box 'Dreamer-ready', "
        "so treat answers as advisory and re-verify anything load-bearing. "
        "TWO rules built into this tool: (1) the photo is prepared for you — "
        "PNG/JPEG, EXIF upright, longest side <= 1568 px, each side >= 280 px, "
        "standard baseline JPEG; pass a local file path or a public http(s) "
        "image URL. (2) the presence gate ships inside the same API call as "
        "the Box, because a Box ALWAYS answers — even an empty photo returns "
        "a confident-looking full-image box. Read gate_passed/"
        "skipped_by_gate FIRST; only trust 'box' when the gate passed. Gate "
        "confidence and box confidence both jitter run-to-run: retry a weak "
        "gate before concluding. Small targets in large images can be missed: "
        "crop first, or ask about a region. One box per call — several "
        "objects need several calls. The image bytes and the target "
        "description are sent to the TypeSafe API."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "Local path of a PNG or JPEG photo. Preferred: no "
                "download, nothing fetched.",
            },
            "image_url": {
                "type": "string",
                "description": "Public http(s) image URL — downloaded to a temp "
                "file and sent to TypeSafe as bytes. Not for private hosts.",
            },
            "target": {
                "type": "string",
                "description": "What to find, e.g. 'the red square in `photo`'. "
                "One instance per call; when several identical items exist, "
                "describe the specific one ('the mug nearest the sink').",
            },
            "state": {
                "type": "string",
                "description": "Optional extra context string the gate and the box "
                "should consider ('front-door camera, night, IR glare expected').",
            },
            "gate_floor": {
                "type": "number",
                "description": "Skip-the-box threshold for the presence gate, 0-1. "
                "Default 0.6. Presence nouls jitter (~±0.05 or worse); a low gate "
                "on real images is a retry signal, not proof of absence.",
            },
            "model": {
                "type": "string",
                "description": "Optional TypeSafe model id; MUST be Jev 1.14 for "
                "Box (default: the vision_model setting, 'jev-preview').",
            },
        },
        "required": ["target"],
    },
}

SCHEMAS = [EVALUATE, CHECK, ROUTE, SCORE, FIND]
