"""Background tasks for the UI. Workers never touch widgets; they only emit signals."""

import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage

from oledify.core.export import deband
from oledify.core.oled import crush_blacks, true_black_percent
from PIL import Image, ImageOps

from oledify.core.pipeline import Settings, _to_rgb8, load_image, process_batch
from oledify.core.resize import fit_image

PREVIEW_MAX_SIDE = 1600
THUMB_MAX_SIDE = 160
THUMB_RESIZABLE_MODES = ("RGB", "RGBA", "L", "LA", "CMYK")
PREVIEW_SEED = 0

# (preview width, preview height, fit mode, focus): identifies one fitted preview frame
FitKey = tuple[int, int, str, tuple[float, float]]


def to_qimage(img: np.ndarray) -> QImage:
    """Copy a uint8 RGB HxWx3 array into a QImage that owns its own memory."""
    img = np.ascontiguousarray(img)
    h, w = img.shape[:2]
    return QImage(img.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def preview_size(w: int, h: int, max_side: int = PREVIEW_MAX_SIDE) -> tuple[int, int]:
    """Size that fits within max_side on the long edge, keeping aspect. Never enlarges."""
    scale = min(1.0, max_side / max(w, h))
    return max(1, round(w * scale)), max(1, round(h * scale))


class WorkerSignals(QObject):
    """Shared, long-lived signal hub owned by the window; emits from workers are queued."""

    # request_id, path, full-resolution RGB array
    loaded = Signal(int, str, object)
    # request_id, path, exception
    load_failed = Signal(int, str, object)
    # request_id, fit key, fitted array, fitted QImage, processed QImage, true black %, elapsed ms
    rendered = Signal(int, object, object, QImage, QImage, float, float)
    render_failed = Signal(int, str)
    # path, thumbnail QImage / exception
    thumbnail_ready = Signal(str, QImage)
    thumbnail_failed = Signal(str, object)
    # done, total targets across the batch
    export_progress = Signal(int, int)
    # output folder, {source Path: [(path, stats), ...] or Exception}, cancelled
    export_finished = Signal(str, object, bool)
    export_failed = Signal(object)


class LoadTask(QRunnable):
    """Load an image file at full resolution."""

    def __init__(self, request_id: int, path: str | Path, signals: WorkerSignals):
        super().__init__()
        self.request_id = request_id
        self.path = str(path)
        self.signals = signals

    def run(self) -> None:
        try:
            self.signals.loaded.emit(self.request_id, self.path, load_image(self.path))
        except Exception as exc:  # report any failure to the UI instead of dying silently
            self.signals.load_failed.emit(self.request_id, self.path, exc)


class PreviewTask(QRunnable):
    """Fit the source to the preview frame (unless a cached fit is given), then crush and deband."""

    def __init__(
        self,
        request_id: int,
        source: np.ndarray,
        fit_key: FitKey,
        fitted: np.ndarray | None,
        threshold: int,
        falloff: int,
        signals: WorkerSignals,
    ):
        super().__init__()
        self.request_id = request_id
        self.source = source
        self.fit_key = fit_key
        self.fitted = fitted
        self.threshold = threshold
        self.falloff = falloff
        self.signals = signals

    def run(self) -> None:
        try:
            start = time.perf_counter()
            fitted = self.fitted
            if fitted is None:
                w, h, mode, focus = self.fit_key
                fitted = fit_image(self.source, w, h, mode=mode, focus=focus)
            out = crush_blacks(fitted, threshold=self.threshold, falloff=self.falloff)
            out = deband(out, seed=PREVIEW_SEED)
            percent = true_black_percent(out)
            original_image, processed_image = to_qimage(fitted), to_qimage(out)
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.signals.rendered.emit(
                self.request_id, self.fit_key, fitted, original_image, processed_image, percent, elapsed_ms
            )
        except Exception as exc:
            self.signals.render_failed.emit(self.request_id, str(exc))


class ThumbnailTask(QRunnable):
    """Decode a file at reduced size for the list thumbnail.

    Exception to "no processing in ui/": uses Pillow's draft()/thumbnail() directly
    for speed, since core has no reduced-size loader yet. Candidate to move into core.
    """

    def __init__(self, path: str, signals: WorkerSignals, max_side: int = THUMB_MAX_SIDE):
        super().__init__()
        self.path = path
        self.signals = signals
        self.max_side = max_side

    def run(self) -> None:
        try:
            size = (self.max_side, self.max_side)
            with Image.open(self.path) as image:
                image.draft("RGB", size)  # JPEG: decode at 1/2, 1/4 or 1/8 scale; no-op otherwise
                image = ImageOps.exif_transpose(image)
                if image.mode not in THUMB_RESIZABLE_MODES:
                    image = _to_rgb8(image)  # e.g. 16-bit or palette: convert before resizing
                image.thumbnail(size, Image.Resampling.LANCZOS)
                thumb = np.asarray(_to_rgb8(image), dtype=np.uint8)
            self.signals.thumbnail_ready.emit(self.path, to_qimage(thumb))
        except Exception as exc:
            self.signals.thumbnail_failed.emit(self.path, exc)


class BatchExportTask(QRunnable):
    """Run the full pipeline for every source and target, reporting progress."""

    def __init__(
        self,
        sources: list[str],
        out_dir: str | Path,
        targets: list[tuple[int, int]],
        settings: Settings,
        overrides: dict[str, Settings],
        cancel: threading.Event,
        signals: WorkerSignals,
    ):
        super().__init__()
        self.sources = list(sources)
        self.out_dir = str(out_dir)
        self.targets = list(targets)
        self.settings = settings
        self.overrides = dict(overrides)
        self.cancel = cancel
        self.signals = signals

    def run(self) -> None:
        try:
            results = process_batch(
                self.sources,
                self.out_dir,
                self.targets,
                self.settings,
                on_progress=self.signals.export_progress.emit,  # queued to the UI thread
                cancel=self.cancel,
                overrides=self.overrides,
            )
            self.signals.export_finished.emit(self.out_dir, results, self.cancel.is_set())
        except Exception as exc:
            self.signals.export_failed.emit(exc)
