"""Image preparation for image-capable Jev tools (jev_find).

Rules from the TypeSafe images docs (https://docs.typesafe.ai/images.md),
each one encodes a way a photo can fail to reach the model:
- PNG or JPEG only; convert GIF/WebP/HEIC/animated files first.
- The API strips EXIF metadata and does NOT apply EXIF orientation, so the
  pixels themselves must be rotated upright (exif_transpose).
- Resize so the longest side is at most 1568 px; reject nothing — shrink.
  Each side must be >= 280 px (the API rejects smaller); pad smaller images
  onto a neutral canvas.
- Prefer a standard 8-bit baseline JPEG; re-export anything damaged.
- Unpredictable references: use a fresh uuid per image per request (the
  reference is matched as plain text anywhere in the request, so `cat` would
  also match inside `category`).

Pillow is the only extra dependency; without it, files already within the
limits pass through and readable errors tell the caller how to fix the rest.
"""

from __future__ import annotations

import base64
import os
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import client
from .client import JevError

PAD_COLOR = (245, 245, 245)


def _open_image(path: str):
    """Open path with Pillow; raise a readable JevError when impossible."""
    try:
        from PIL import Image, ImageOps  # noqa: PLC0415 — optional dep
    except ImportError as e:
        raise JevError(
            "Reading/editing images needs Pillow (pip install pillow): "
            "PNG/JPEG, rotate EXIF upright, longest side <= 1568 px, "
            "each side >= 280 px before sending."
        ) from e
    try:
        return Image.open(path)
    except Exception as e:  # noqa: BLE001 — surface any bad-file cause
        raise JevError(
            f"Cannot open image {path!r}: {e}. The API accepts standard "
            "baseline JPEG or PNG only — re-export the file in a standard "
            "editor and retry."
        ) from e


def prepare_image(path: str, out_dir: str | None = None) -> tuple[str, bool]:
    """Return (path of a sendable copy, whether it is PNG) after prep.

    Never mutates the source file. The output is a baseline JPEG (or PNG
    when the source is PNG within limits) written next to the source.
    """
    img = _open_image(path)
    try:
        from PIL import Image, ImageOps  # noqa: PLC0415

        animated = getattr(img, "is_animated", False)
        if animated:
            raise JevError(
                f"{path!r} is an animated image; export the frame you want "
                "as a still PNG or JPEG first."
            )
        img = ImageOps.exif_transpose(img)
        fmt = (img.format or "").upper()
        w, h = img.size
        needs_resize = w > client.MAX_IMAGE_SIDE or h > client.MAX_IMAGE_SIDE
        needs_pad = w < client.MIN_IMAGE_SIDE or h < client.MIN_IMAGE_SIDE
        keep_png = fmt == "PNG" and not needs_resize and not needs_pad

        if needs_resize or needs_pad or fmt != "JPEG" and not keep_png:
            img = img.convert("RGB")
            if needs_resize:
                img.thumbnail((client.MAX_IMAGE_SIDE, client.MAX_IMAGE_SIDE))
            if needs_pad:
                canvas = Image.new(
                    "RGB",
                    (max(w, client.MIN_IMAGE_SIDE), max(h, client.MIN_IMAGE_SIDE)),
                    PAD_COLOR,
                )
                w, h = img.size
                canvas.paste(img, ((canvas.width - w) // 2, (canvas.height - h) // 2))
                img = canvas
        base = Path(out_dir) if out_dir else Path(path).parent
        suffix = ".jev-ready.png" if keep_png else ".jev-ready.jpg"
        dest = base / (Path(path).stem + suffix)
        if keep_png:
            img.save(dest, "PNG")
        else:
            img.save(dest, "JPEG", quality=90)
        size_ok = dest.stat().st_size <= 16 * 1024 * 1024
        if not size_ok:
            raise JevError(
                f"{dest} is over the 16 MiB per-file limit; save a smaller "
                "copy and send that."
            )
        return str(dest), keep_png
    finally:
        img.close()


def asset_and_reference(prepared_path: str, keep_png: bool) -> tuple[str, dict]:
    """Return (reference string, assets entry) for one prepared file."""
    ref = uuid.uuid4().hex
    fmt = "png" if keep_png else "jpg"
    data = base64.b64encode(Path(prepared_path).read_bytes()).decode("ascii")
    return ref, {"type": "image", "format": fmt, "data_base64": data}


def local_image_to_asset(path: str) -> tuple[str, dict]:
    """Prepare an image file and return (reference, asset) ready to send."""
    prepared, keep_png = prepare_image(path)
    return asset_and_reference(prepared, keep_png)


def download_to_temp(url: str) -> str:
    """Fetch an http(s) image to a temp file and return its path.

    Loopback/LAN hosts are refused: the model gets the image bytes either
    way, and a raw fetch here would hand private network files to an
    external API.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise JevError("image_url must be http(s); got " + repr(parsed.scheme))
    host = parsed.hostname or ""
    if host in ("localhost", "::1") or _is_private_host(host):
        raise JevError(
            "image_url points at a private/loopback host; download it "
            "locally and pass image_path instead."
        )
    import tempfile  # noqa: PLC0415

    url_path = parsed.path or "/image"
    ext = Path(url_path).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        ext = ".jpg"
    fd, dest = tempfile.mkstemp(prefix="jev-", suffix=ext)
    os.close(fd)
    try:
        urllib.request.urlretrieve(url, dest)  # noqa: S310 — https checked above
    except Exception as e:
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise JevError(f"Cannot download image {url!r}: {e}") from e
    return dest


def _is_private_host(host: str) -> bool:
    import ipaddress  # noqa: PLC0415

    try:
        return ipaddress.ip_address(host).is_private or ipaddress.ip_address(
            host
        ).is_loopback
    except ValueError:
        return False


def make_state(image_ref: str, extra_state: Any = None) -> Any:
    """Build the request state: the image reference plus optional context.

    One image, referenced once, in the state — shared by every question —
    is both the cheapest and the most accurate placement per the docs.
    """
    if extra_state in (None, ""):
        return {"photo": image_ref}
    if isinstance(extra_state, str):
        return {"photo": image_ref, "context": extra_state}
    if isinstance(extra_state, dict):
        if "photo" in extra_state:
            raise JevError("extra_state cannot use the reserved key 'photo'.")
        return {"photo": image_ref, **extra_state}
    raise JevError("extra_state must be a string or dict.")