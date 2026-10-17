#!/usr/bin/env python3
"""Unit + live tests for jev_find (Jev 1.14 vision: gate + box).

Part 1 runs offline: image prep normalization, question validation, asset
validation, and jev_find arg handling — no key and no API calls.
Part 2 runs live only when TYPESAFE_API_KEY resolves (env or ~/.hermes/.env):
3 synthetic-image calls against api.typesafe.ai (jev-1.14.0, ~0.3 s each,
well under $0.001 total).

Run with any python3 (+ Pillow for Part 1; Part 2 skips without Pillow
too, since find() needs it). Assertions use bands, not exact values:
probabilities drift run-to-run (observed ±0.05 on presence nouls).
Exit codes: 0 pass, 1 fail.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV_FILE = Path.home() / ".hermes" / ".env"

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}"
          + (f" — {detail}" if detail and not cond else ""), flush=True)
    if not cond:
        failures.append(name)


def load_package():
    """Import the repo as package 'jev' (the install name), via a temp link."""
    import importlib

    tmp = Path(tempfile.mkdtemp(prefix="jev-test-pkg-"))
    link = tmp / "jev"
    try:
        link.symlink_to(REPO, target_is_directory=True)
    except OSError:
        shutil.copytree(REPO, link, ignore=shutil.ignore_patterns(".git", "tests"))
    sys.path.insert(0, str(tmp))
    os.environ.setdefault("JEV_TEST_TMP", str(tmp))
    return importlib.import_module("jev")


def resolve_key() -> str | None:
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if key:
        return key
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("TYPESAFE_API_KEY="):
                key = line.split("=", 1)[1].strip()
                os.environ["TYPESAFE_API_KEY"] = key
                return key
    return None


def synth_images() -> dict[str, Path]:
    """Build the test set; returns {} when Pillow is missing."""
    from PIL import Image  # noqa: PLC0415

    d = Path(tempfile.mkdtemp(prefix="jev-test-img-"))
    # present target: red square, GT box left .35 right .65 top .30 bottom .70
    img = Image.new("RGB", (600, 600), (255, 255, 255))
    for x in range(210, 391):
        for y in range(180, 421):
            img.putpixel((x, y), (220, 30, 30))
    img.save(d / "present.png", "PNG")
    Image.new("RGB", (600, 600), (240, 240, 240)).save(d / "blank.jpg", "JPEG")
    Image.new("RGB", (200, 200), (0, 0, 0)).save(d / "tiny.png", "PNG")
    Image.new("RGB", (1800, 900), (0, 0, 0)).save(d / "big.jpg", "JPEG")
    frames = [Image.new("P", (300, 300), c) for c in ((0, 0, 0), (255, 0, 0))]
    frames[0].save(d / "anim.gif", save_all=True, append_images=frames[1:], loop=0)
    (d / "bad.jpg").write_text("not an image")
    return {p.stem: p for p in d.iterdir()}


def part1_offline(jev) -> None:
    print("--- Part 1: offline ---", flush=True)

    # 1. image prep normalization
    try:
        imgs = synth_images()
        have_pil = True
    except ImportError:
        have_pil = False
    if not have_pil:
        check("images: skipped (no Pillow)", True)
    else:
        path, keep = jev.images.prepare_image(str(imgs["present"]))
        check("images: 600x600 passes through within limits", keep is False)
        path2, _ = jev.images.prepare_image(str(imgs["tiny"]))
        from PIL import Image  # noqa: PLC0415

        w, h = Image.open(path2).size
        check("images: sub-280 image padded up", w >= 280 and h >= 280, f"{w}x{h}")
        path3, _ = jev.images.prepare_image(str(imgs["big"]))
        w, h = Image.open(path3).size
        check("images: oversized shrunk to <= 1568", max(w, h) <= 1568, f"{w}x{h}")
        for bad in ("anim", "bad"):
            try:
                jev.images.prepare_image(str(imgs[bad]))
                check(f"images: {bad} rejected", False, "no error raised")
            except jev.client.JevError:
                check(f"images: {bad} rejected with readable error", True)

    # 2. question validation: box shapes
    ok = {"good": {"type": "box", "instructions": "the red square in `photo`"}}
    n = jev.client.normalize_questions(ok)
    check("questions: box accepted without criteria", n["good"]["type"] == "box")
    for bad, why in [
        ({"b": {"type": "box", "instructions": "x", "criteria": {"a": "b"}}}, "box+criteria"),
        ({"b": {"type": "box"}}, "box missing instructions"),
        ({"b": {"type": "banana", "instructions": "x"}}, "unknown type"),
    ]:
        try:
            jev.client.normalize_questions(bad)
            check(f"questions: rejects {why}", False, "no error raised")
        except jev.client.JevError:
            check(f"questions: rejects {why}", True)

    # 3. asset validation: unreferenced asset fails BEFORE any network call
    try:
        jev.client.build_payload(
            state={"photo": "deadbeef-photo"},
            questions={"b": {"type": "box", "instructions": "the red square in `photo`"}},
            assets={"otherbeef-photo": {"type": "image", "format": "png", "data_base64": "x"}},
        )
        check("assets: unreferenced asset rejected locally", False, "no error raised")
    except jev.client.JevError as e:
        check("assets: unreferenced asset rejected locally", "not referenced" in str(e))
    # referenced assets pass validation and add the images header
    payload, headers = jev.client.build_payload(
        state={"photo": "deadbeef-photo"},
        questions={"b": {"type": "box", "instructions": "the red square in `photo`"}},
        assets={"deadbeef-photo": {"type": "image", "format": "png", "data_base64": "x"}},
    )
    check("assets: referenced assets attach + images header set",
          payload.get("assets") and headers.get("TypeSafe-Danger-Zone-Images") == "true")
    payload, headers = jev.client.build_payload(state="x", questions={}, model="jev-latest")
    check("assets: text-only request carries NO images header",
          "TypeSafe-Danger-Zone-Images" not in headers)

    # 4. jev_find arg validation (no API involved)
    out = json.loads(jev.tools.find({}))
    check("find: missing target -> error JSON", "error" in out)
    out = json.loads(jev.tools.find({"target": "the red square", "gate_floor": "x"}))
    check("find: bad gate_floor -> error JSON", "gate_floor" in out["error"])
    out = json.loads(jev.tools.find({"target": "x", "image_path": "/nonexistent/x.jpg"}))
    check("find: nonexistent path -> error JSON", "error" in out)
    out = json.loads(jev.tools.find({"target": "x", "image_path": str(REPO / "client.py")}))
    check("find: non-image file -> readable error", "error" in out)


def part2_live(jev) -> None:
    if not resolve_key():
        print("--- Part 2: live SKIPPED (no TYPESAFE_API_KEY) ---", flush=True)
        return
    try:
        imgs = synth_images()
    except ImportError:
        print("--- Part 2: live SKIPPED (no Pillow) ---", flush=True)
        return
    print("--- Part 2: live (3 calls, jev-1.14.0) ---", flush=True)

    out = json.loads(jev.tools.find({"target": "the red square", "image_path": str(imgs["present"])}))
    print("    present:", json.dumps(out)[:220])
    if "error" in out:
        check("find live: present-target call", False, str(out)[:160])
    else:
        check("find live: gate passed", out["gate_passed"], f"present={out['present']}")
        box = out.get("box") or {}
        if box:
            close = (abs(box["left"] - 0.35) <= 0.05 and abs(box["right"] - 0.65) <= 0.05
                     and abs(box["top"] - 0.30) <= 0.05 and abs(box["bottom"] - 0.70) <= 0.05)
            check("find live: box within ±0.05 of ground truth", close, json.dumps(box))
            check("find live: echoed model is 1.14", "1.14" in out["model"], out["model"])
        else:
            check("find live: box present", False, "no box in answer")

    out = json.loads(jev.tools.find({"target": "the purple elephant", "image_path": str(imgs["blank"])}))
    print("    ghost:", json.dumps(out)[:220])
    if "error" in out:
        check("find live: ghost call", False, str(out)[:160])
    else:
        check("find live: ghost gate fails", not out["gate_passed"], f"present={out['present']}")
        check("find live: ghost answer flagged skipped", out["skipped_by_gate"])

    out = json.loads(jev.tools.find({"target": "the red square", "image_path": str(imgs["present"]),
                                     "model": "jev-latest"}))
    print("    box-on-1.13:", json.dumps(out)[:220])
    check("find live: box question on jev-latest fails gracefully", "error" in out)


def main() -> None:
    jev = load_package()
    part1_offline(jev)
    part2_live(jev)
    print()
    if failures:
        print(f"FAILED ({len(failures)}): " + "; ".join(failures))
        sys.exit(1)
    print("OK — all checks passed")
    shutil.rmtree(os.environ.get("JEV_TEST_TMP", "."), ignore_errors=True)


if __name__ == "__main__":
    main()