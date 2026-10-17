"""Jev plugin — TypeSafe System One questions for Hermes Agent.

Registers four tools backed by the TypeSafe Jev API:
  jev_evaluate — many mixed questions in one call
  jev_check    — single yes/no (noul)
  jev_route    — pick one option (choice)
  jev_score    — ordered rubric rating (score)
When vision_enabled is on, also: jev_find — presence-gated Box on an image (Jev 1.14).
"""

from __future__ import annotations

import logging

from . import client, tools
from .schemas import CHECK, EVALUATE, FIND, ROUTE, SCORE

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Called exactly once by Hermes at startup."""
    common = {"toolset": "jev"}
    ctx.register_tool(name="jev_evaluate", schema=EVALUATE,
                      handler=lambda args, **kw: tools.evaluate(args, ctx=ctx, **kw), **common)
    ctx.register_tool(name="jev_check", schema=CHECK,
                      handler=lambda args, **kw: tools.check(args, ctx=ctx, **kw), **common)
    ctx.register_tool(name="jev_route", schema=ROUTE,
                      handler=lambda args, **kw: tools.route(args, ctx=ctx, **kw), **common)
    ctx.register_tool(name="jev_score", schema=SCORE,
                      handler=lambda args, **kw: tools.score(args, ctx=ctx, **kw), **common)

    # Vision tools register only when enabled: images/Box are Dreamer-ready
    # (format can change between releases) and need Jev 1.14.
    try:
        if ctx.get_config("vision_enabled", default=False):
            ctx.register_tool(name="jev_find", schema=FIND,
                              handler=lambda args, **kw: tools.find(args, ctx=ctx, **kw), **common)
            logger.info("jev: vision enabled — jev_find registered (model %s)",
                        ctx.get_config("vision_model", default="jev-preview"))
    except Exception:  # noqa: BLE001 — vision must never break plugin load
        logger.debug("jev: vision tools not registered", exc_info=True)

    # Bundle the usage skill so the agent can load it on demand.
    try:
        from pathlib import Path

        skills_dir = Path(__file__).parent / "skills"
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)
    except Exception:  # noqa: BLE001 — skill bundling must never break plugin load
        logger.debug("jev: skill bundling skipped", exc_info=True)

    logger.info("jev: registered 4 TypeSafe Jev tools (evaluate, check, route, score)")
