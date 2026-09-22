"""Processing pipeline: load -> fit -> crush -> deband -> save. No Qt."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from oledify.core.export import deband, output_name, save_image
from oledify.core.oled import crush_blacks, true_black_percent
from oledify.core.resize import fit_image, is_upscale

_HIGH_BIT_MODES = ("I", "I;16", "I;16B", "I;16L", "I;16N")


@dataclass
class Settings:
    threshold: float = 16
    falloff: float = 24
    mode: str = "crop"
    focus: tuple[float, float] = (0.5, 0.5)
    deband: bool = True
    fmt: str = "png"
    quality: int = 95


def _to_rgb8(image: Image.Image) -> Image.Image:
    """Convert any Pillow image to 8-bit RGB, compositing transparency onto black."""
    if image.mode in _HIGH_BIT_MODES:
        # Pillow's own conversion clips 16-bit values instead of scaling them.
        data = np.asarray(image, dtype=np.float64)
        grey = np.clip(np.rint(data / 257.0), 0, 255).astype(np.uint8)
        return Image.fromarray(grey).convert("RGB")
    if image.mode == "F":
        grey = np.clip(np.rint(np.asarray(image)), 0, 255).astype(np.uint8)
        return Image.fromarray(grey).convert("RGB")

    has_alpha = "A" in image.getbands() or "a" in image.getbands() or "transparency" in image.info
    if has_alpha:
        rgba = image.convert("RGBA")
        black = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        return Image.alpha_composite(black, rgba).convert("RGB")
    return image.convert("RGB")


def load_image(path: str | Path) -> np.ndarray:
    """Load an image file as a uint8 RGB HxWx3 array, upright per its EXIF orientation."""
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        return np.array(_to_rgb8(image), dtype=np.uint8)


def process(img: np.ndarray, target_w: int, target_h: int, settings: Settings) -> tuple[np.ndarray, dict]:
    """Fit, crush blacks and optionally deband. Returns (image, stats)."""
    src_h, src_w = img.shape[:2]
    out = fit_image(img, target_w, target_h, mode=settings.mode, focus=settings.focus)
    out = crush_blacks(out, threshold=settings.threshold, falloff=settings.falloff)
    if settings.deband:
        out = deband(out)
    stats = {
        "true_black_percent": true_black_percent(out),
        "upscaled": is_upscale(src_w, src_h, target_w, target_h, settings.mode),
        "src_size": (src_w, src_h),
        "out_size": (target_w, target_h),
    }
    return out, stats


def process_file(
    src: str | Path,
    out_dir: str | Path,
    targets: list[tuple[int, int]],
    settings: Settings,
) -> list[tuple[Path, dict]]:
    """Load src once, then process and save it for each (width, height) target."""
    img = load_image(src)
    out_dir = Path(out_dir)
    results = []
    for target_w, target_h in targets:
        out, stats = process(img, target_w, target_h, settings)
        path = out_dir / output_name(src, target_w, target_h, settings.fmt)
        results.append((save_image(out, path, fmt=settings.fmt, quality=settings.quality), stats))
    return results
