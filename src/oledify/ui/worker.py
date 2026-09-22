"""Background tasks for the UI. Workers never touch widgets; they only emit signals."""

import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage

from oledify.core.export import deband
from oledify.core.oled import crush_blacks, true_black_percent
from oledify.core.pipeline import Settings, load_image, process_file
from oledify.core.resize import fit_image

PREVIEW_MAX_SIDE = 1600
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
    load_failed = Signal(int, str, str)
    # request_id, fit key, fitted array, fitted QImage, processed QImage, true black %, elapsed ms
    rendered = Signal(int, object, object, QImage, QImage, float, float)
    render_failed = Signal(int, str)
    # output folder, [(path, stats), ...]
    export_finished = Signal(str, object)
    export_failed = Signal(str)


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
            self.signals.load_failed.emit(self.request_id, self.path, str(exc))


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


class ExportTask(QRunnable):
    """Run the full pipeline for every target and save the results."""

    def __init__(
        self,
        src: str | Path,
        out_dir: str | Path,
        targets: list[tuple[int, int]],
        settings: Settings,
        signals: WorkerSignals,
    ):
        super().__init__()
        self.src = str(src)
        self.out_dir = str(out_dir)
        self.targets = list(targets)
        self.settings = settings
        self.signals = signals

    def run(self) -> None:
        try:
            results = process_file(self.src, self.out_dir, self.targets, self.settings)
            self.signals.export_finished.emit(self.out_dir, results)
        except Exception as exc:
            self.signals.export_failed.emit(str(exc))
