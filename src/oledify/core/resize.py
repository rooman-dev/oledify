"""Resize images to a target size with crop, bars or blur fit modes.

Images are uint8 RGB numpy arrays of shape HxWx3. Uses Pillow; never imports Qt.
"""

import numpy as np
from PIL import Image, ImageFilter

MODES = ("crop", "bars", "blur")
BLUR_RADIUS_FRACTION = 0.02
BLUR_BRIGHTNESS = 0.4


def _check_image(img: np.ndarray) -> None:
    if not isinstance(img, np.ndarray) or img.dtype != np.uint8 or img.ndim != 3 or img.shape[2] != 3:
        raise ValueError("expected a uint8 RGB array of shape HxWx3")


def _check_size(*sizes: int) -> None:
    for size in sizes:
        if isinstance(size, bool) or not isinstance(size, (int, np.integer)) or size <= 0:
            raise ValueError(f"sizes must be positive integers, got {size!r}")


def _check_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")


def _cover_scale(src_w: int, src_h: int, target_w: int, target_h: int) -> float:
    return max(target_w / src_w, target_h / src_h)


def _contain_scale(src_w: int, src_h: int, target_w: int, target_h: int) -> float:
    return min(target_w / src_w, target_h / src_h)


def _resize(img: Image.Image, w: int, h: int) -> Image.Image:
    if img.size == (w, h):
        return img
    return img.resize((w, h), Image.Resampling.LANCZOS)


def _cover(img: Image.Image, target_w: int, target_h: int, focus: tuple[float, float]) -> Image.Image:
    src_w, src_h = img.size
    scale = _cover_scale(src_w, src_h, target_w, target_h)
    new_w = max(target_w, round(src_w * scale))
    new_h = max(target_h, round(src_h * scale))
    scaled = _resize(img, new_w, new_h)
    left = min(max(round(focus[0] * (new_w - target_w)), 0), new_w - target_w)
    top = min(max(round(focus[1] * (new_h - target_h)), 0), new_h - target_h)
    return scaled.crop((left, top, left + target_w, top + target_h))


def _contain(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    src_w, src_h = img.size
    scale = _contain_scale(src_w, src_h, target_w, target_h)
    new_w = min(target_w, max(1, round(src_w * scale)))
    new_h = min(target_h, max(1, round(src_h * scale)))
    return _resize(img, new_w, new_h)


def is_upscale(src_w: int, src_h: int, target_w: int, target_h: int, mode: str) -> bool:
    """True if the chosen mode has to enlarge the source image.

    For "blur" this refers to the foreground; the blurred background is ignored.
    """
    _check_size(src_w, src_h, target_w, target_h)
    _check_mode(mode)
    if mode == "crop":
        return _cover_scale(src_w, src_h, target_w, target_h) > 1
    return _contain_scale(src_w, src_h, target_w, target_h) > 1


def fit_image(
    img: np.ndarray,
    target_w: int,
    target_h: int,
    mode: str = "crop",
    focus: tuple[float, float] = (0.5, 0.5),
) -> np.ndarray:
    """Return a new target_h x target_w x 3 uint8 array fitted with the given mode.

    crop: scale to cover, then crop around focus (x, y in 0-1).
    bars: scale to fit inside, centred on a pure black canvas.
    blur: like bars, on a blurred, darkened, cover-scaled copy of the source.
    """
    _check_image(img)
    _check_size(target_w, target_h)
    _check_mode(mode)
    if len(focus) != 2 or not all(0.0 <= f <= 1.0 for f in focus):
        raise ValueError(f"focus must be (x, y) with values in 0-1, got {focus!r}")

    src = Image.fromarray(img, mode="RGB")

    if mode == "crop":
        return np.array(_cover(src, target_w, target_h, focus), dtype=np.uint8)

    foreground = _contain(src, target_w, target_h)
    fg_w, fg_h = foreground.size
    if (fg_w, fg_h) == (target_w, target_h):
        return np.array(foreground, dtype=np.uint8)

    if mode == "bars":
        canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
    else:
        background = _cover(src, target_w, target_h, (0.5, 0.5))
        background = background.filter(ImageFilter.GaussianBlur(BLUR_RADIUS_FRACTION * target_w))
        canvas = Image.eval(background, lambda v: round(v * BLUR_BRIGHTNESS))

    canvas.paste(foreground, ((target_w - fg_w) // 2, (target_h - fg_h) // 2))
    return np.array(canvas, dtype=np.uint8)
