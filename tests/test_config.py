"""Offline: plugin settings survive Hermes' reserved settings roots.

Hermes rejects plugin-relative settings keys whose first segment is a core root
(`model`, `plugins`, `security`, `settings`): `ctx.get_config("model")` raises
ValueError. These tests use a fake ctx with the same contract. No network.

Run from the repo root:  python -m unittest discover -s tests -p "test_*.py"
"""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Load the plugin as a package (it uses relative imports, as Hermes' loader does).
if "jevpkg" not in sys.modules:
    pkg = types.ModuleType("jevpkg")
    pkg.__path__ = [str(ROOT)]
    sys.modules["jevpkg"] = pkg
    for _mod in ("client", "tools"):
        _spec = importlib.util.spec_from_file_location(f"jevpkg.{_mod}", ROOT / f"{_mod}.py")
        _m = importlib.util.module_from_spec(_spec)
        sys.modules[f"jevpkg.{_mod}"] = _m
        _spec.loader.exec_module(_m)

from jevpkg import client, tools  # noqa: E402

RESERVED = {"model", "plugins", "security", "settings"}


class FakeCtx:
    """Mimics hermes_cli PluginContext.get_config, including reserved-root rejection."""

    def __init__(self, settings: dict):
        self.settings = settings
        self.asked: list[str] = []

    def get_config(self, key, default=None):
        self.asked.append(key)
        if key.split(".")[0].lower() in RESERVED:
            raise ValueError("Expected a plugin-relative config key")
        return self.settings.get(key, default)


class ConfigTests(unittest.TestCase):
    def test_settings_apply_without_touching_reserved_keys(self):
        ctx = FakeCtx({"jev_model": "jev-1.14.0", "timeout_seconds": 7, "max_questions": 3})
        cfg = tools._config(ctx)
        self.assertEqual(cfg["model"], "jev-1.14.0")
        self.assertEqual(cfg["timeout_seconds"], 7)
        self.assertEqual(cfg["max_questions"], 3)
        self.assertFalse(RESERVED & {k.split(".")[0] for k in ctx.asked})

    def test_defaults_when_unset(self):
        cfg = tools._config(FakeCtx({}))
        self.assertEqual(cfg["model"], client.DEFAULT_MODEL)
        self.assertEqual(cfg["timeout_seconds"], client.DEFAULT_TIMEOUT)
        self.assertEqual(cfg["max_questions"], 20)

    def test_one_bad_value_does_not_drop_the_others(self):
        ctx = FakeCtx({"jev_model": "jev-1.13.0", "timeout_seconds": "not-a-number", "max_questions": 5})
        cfg = tools._config(ctx)
        self.assertEqual(cfg["model"], "jev-1.13.0")
        self.assertEqual(cfg["timeout_seconds"], client.DEFAULT_TIMEOUT)
        self.assertEqual(cfg["max_questions"], 5)

    def test_no_ctx(self):
        cfg = tools._config(None)
        self.assertEqual(cfg["model"], client.DEFAULT_MODEL)

    def test_max_questions_setting_is_enforced(self):
        ctx = FakeCtx({"max_questions": 1})
        qs = {
            "a": {"type": "noul", "instructions": "Is it A?"},
            "b": {"type": "noul", "instructions": "Is it B?"},
        }
        out = tools.evaluate({"state": "x", "questions": qs}, ctx=ctx)
        self.assertIn("Too many questions", out)
        self.assertIn("max is 1", out)


    def test_non_positive_numbers_fall_back_to_defaults(self):
        for bad in (-1, 0, "-5"):
            cfg = tools._config(FakeCtx({"max_questions": bad, "timeout_seconds": bad}))
            self.assertEqual(cfg["max_questions"], 20, bad)
            self.assertEqual(cfg["timeout_seconds"], client.DEFAULT_TIMEOUT, bad)

    def test_negative_limit_does_not_reject_every_request(self):
        offline = mock.patch.object(client, "call_system_one", side_effect=client.JevError("offline"))
        with offline:
            out = tools.evaluate({"state": "x", "questions": {"a": {"type": "noul", "instructions": "Is it A?"}}},
                                 ctx=FakeCtx({"max_questions": -1}))
        self.assertNotIn("Too many questions", out)


if __name__ == "__main__":
    unittest.main()
