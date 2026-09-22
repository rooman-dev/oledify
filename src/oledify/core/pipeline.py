"""Processing pipeline: load -> fit -> crush -> deband -> save. No Qt."""

import os
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from oledify.core.export import deband, output_name, save_image
from oledify.core.oled import crush_blacks, true_black_percent
from oledify.core.resize import fit_image, is_upscale

_HIGH_BIT_MODES = ("I", "I;16", "I;16B", "I;16L", "I;16N")

ProgressCallback = Callable[[int, int], None]


class Cancelled(Exception):
    """Stored in process_batch results for a source skipped because the batch was cancelled."""


@dataclass
class Settings:
    threshold: float = 16
    falloff: float = 24
    mode: str = "crop"
    focus: tuple[float, float] = (0.5, 0.5)
    deband: bool = True
    fmt: str = "png"
    quality: int = 95
    seed: int | None = None


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
        out = deband(out, seed=settings.seed)
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
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> list[tuple[Path, dict]]:
    """Load src once, then process and save it for each (width, height) target.

    Targets run in parallel threads (NumPy and Pillow release the GIL for the heavy
    work); results are returned in the same order as targets.

    on_progress(done, total) is called after each target is saved; calls are
    serialised and done only increases. If cancel is set, targets not yet started
    are skipped and left out of the results; running ones finish.
    """
    if not targets:
        return []
    img = load_image(src)
    out_dir = Path(out_dir)
    total = len(targets)
    done = 0
    lock = threading.Lock()

    def run(target: tuple[int, int]) -> tuple[Path, dict] | None:
        nonlocal done
        if cancel is not None and cancel.is_set():
            return None
        target_w, target_h = target
        out, stats = process(img, target_w, target_h, settings)
        path = out_dir / output_name(src, target_w, target_h, settings.fmt)
        result = save_image(out, path, fmt=settings.fmt, quality=settings.quality), stats
        with lock:
            done += 1
            if on_progress is not None:
                on_progress(done, total)
        return result

    with ThreadPoolExecutor(max_workers=min(len(targets), os.cpu_count() or 4, 4)) as pool:
        results = list(pool.map(run, targets))
    return [result for result in results if result is not None]


def process_batch(
    sources: list[str | Path],
    out_dir: str | Path,
    targets: list[tuple[int, int]],
    settings: Settings,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    overrides: Mapping[str | Path, Settings] | None = None,
) -> dict[Path, list[tuple[Path, dict]] | Exception]:
    """Run process_file for each source; one failing source never stops the others.

    Returns {source: results or the exception it raised}, in source order (duplicates
    removed). Sources skipped because cancel was set map to a Cancelled exception.
    on_progress(done, total) counts targets across the whole batch, where
    total = sources x targets; a failed source counts all its targets as done.
    overrides gives per-source Settings (e.g. a crop focus), falling back to settings.
    """
    unique = list(dict.fromkeys(Path(src) for src in sources))
    per_source = {Path(src): value for src, value in (overrides or {}).items()}
    total = len(unique) * len(targets)
    results: dict[Path, list[tuple[Path, dict]] | Exception] = {}

    for index, src in enumerate(unique):
        if cancel is not None and cancel.is_set():
            results[src] = Cancelled(f"skipped {src.name}: batch cancelled")
            continue
        offset = index * len(targets)

        def progress(done: int, _total: int, offset: int = offset) -> None:
            on_progress(offset + done, total)

        try:
            results[src] = process_file(
                src,
                out_dir,
                targets,
                per_source.get(src, settings),
                on_progress=progress if on_progress is not None else None,
                cancel=cancel,
            )
        except Exception as exc:  # any failure is reported for this source; carry on with the rest
            results[src] = exc
            if on_progress is not None and targets:
                on_progress(offset + len(targets), total)
    return results
