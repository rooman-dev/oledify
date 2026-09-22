"""Background tasks for the UI. Workers never touch widgets; they only emit signals."""

import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage

from oledify.core.export import deband
from oledify.core.oled import crush_blacks, true_black_percent
from oledify.core.pipeline import load_image
from oledify.core.resize import fit_image

PREVIEW_MAX_SIDE = 1600
PREVIEW_SEED = 0


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

    # request_id, path, (src_w, src_h), preview array, original QImage
    loaded = Signal(int, str, tuple, object, QImage)
    load_failed = Signal(int, str, str)
    # request_id, processed QImage, true black %, elapsed ms
    rendered = Signal(int, QImage, float, float)
    render_failed = Signal(int, str)


class LoadTask(QRunnable):
    """Load an image file and build the downscaled preview source."""

    def __init__(self, request_id: int, path: str | Path, signals: WorkerSignals):
        super().__init__()
        self.request_id = request_id
        self.path = str(path)
        self.signals = signals

    def run(self) -> None:
        try:
            img = load_image(self.path)
            src_h, src_w = img.shape[:2]
            w, h = preview_size(src_w, src_h)
            preview = img if (w, h) == (src_w, src_h) else fit_image(img, w, h, mode="crop")
            self.signals.loaded.emit(self.request_id, self.path, (src_w, src_h), preview, to_qimage(preview))
        except Exception as exc:  # report any failure to the UI instead of dying silently
            self.signals.load_failed.emit(self.request_id, self.path, str(exc))


class PreviewTask(QRunnable):
    """Run the black crush (and fixed-seed deband) on the preview source."""

    def __init__(self, request_id: int, preview: np.ndarray, threshold: int, falloff: int, signals: WorkerSignals):
        super().__init__()
        self.request_id = request_id
        self.preview = preview
        self.threshold = threshold
        self.falloff = falloff
        self.signals = signals

    def run(self) -> None:
        try:
            start = time.perf_counter()
            out = crush_blacks(self.preview, threshold=self.threshold, falloff=self.falloff)
            out = deband(out, seed=PREVIEW_SEED)
            percent = true_black_percent(out)
            image = to_qimage(out)
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.signals.rendered.emit(self.request_id, image, percent, elapsed_ms)
        except Exception as exc:
            self.signals.render_failed.emit(self.request_id, str(exc))
