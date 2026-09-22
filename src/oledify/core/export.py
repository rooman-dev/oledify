"""Export helpers: debanding, output file names and saving. Pillow + NumPy only."""

from pathlib import Path

import numpy as np
from PIL import Image

from oledify.core.oled import luminance

FORMATS = ("png", "jpg", "webp")
_PIL_FORMATS = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}
DEBAND_MAX_LUMINANCE = 64


def _check_fmt(fmt: str) -> None:
    if fmt not in FORMATS:
        raise ValueError(f"fmt must be one of {FORMATS}, got {fmt!r}")


def deband(img: np.ndarray, strength: int = 1, seed: int | None = None) -> np.ndarray:
    """Add +/-strength grey noise to dark pixels (0 < luminance < 64) to hide banding.

    Pure black (0, 0, 0) pixels are never touched. Returns a new array.
    """
    if isinstance(strength, bool) or not isinstance(strength, (int, np.integer)) or strength < 0:
        raise ValueError(f"strength must be a non-negative integer, got {strength!r}")
    lum = luminance(img)
    out = img.copy()
    mask = (lum > 0) & (lum < DEBAND_MAX_LUMINANCE)
    if strength == 0 or not mask.any():
        return out
    rng = np.random.default_rng(seed)
    noise = rng.integers(-strength, strength + 1, size=int(np.count_nonzero(mask)), dtype=np.int16)
    out[mask] = np.clip(img[mask].astype(np.int16) + noise[:, None], 0, 255).astype(np.uint8)
    return out


def output_name(src_path: str | Path, w: int, h: int, fmt: str) -> str:
    """Build "<stem>_<w>x<h>_oled.<ext>" for the given source path."""
    _check_fmt(fmt)
    return f"{Path(src_path).stem}_{w}x{h}_oled.{fmt}"


def _save_options(fmt: str, quality: int) -> dict:
    if fmt == "png":
        return {"compress_level": 6}
    if fmt == "jpg":
        return {"quality": quality, "subsampling": 0}
    if quality == 100:
        return {"lossless": True, "method": 4}
    return {"quality": quality, "method": 4}


def save_image(img: np.ndarray, path: str | Path, fmt: str = "png", quality: int = 95) -> Path:
    """Save img to path, creating parent folders and never overwriting.

    If path exists, "_1", "_2", ... is appended to the stem. Returns the path written.
    """
    _check_fmt(fmt)
    if isinstance(quality, bool) or not isinstance(quality, (int, np.integer)) or not 1 <= quality <= 100:
        raise ValueError(f"quality must be an integer in 1-100, got {quality!r}")
    pil_image = Image.fromarray(img)
    if pil_image.mode != "RGB":
        raise ValueError("expected a uint8 RGB array of shape HxWx3")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate, n = path, 0
    while True:
        try:
            handle = open(candidate, "xb")  # exclusive create: no race with other writers
            break
        except FileExistsError:
            n += 1
            candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")

    try:
        with handle:
            pil_image.save(handle, format=_PIL_FORMATS[fmt], **_save_options(fmt, quality))
    except BaseException:
        candidate.unlink(missing_ok=True)
        raise
    return candidate
