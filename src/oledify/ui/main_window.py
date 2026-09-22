"""Main application window: open an image, tune the black crush live, export presets."""

import os
from dataclasses import replace
from pathlib import Path

import numpy as np
from PySide6.QtCore import QThreadPool, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QImage
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from oledify import __version__
from oledify.core.pipeline import Settings
from oledify.core.resize import is_upscale
from oledify.ui.compare_view import CompareView
from oledify.ui.export_panel import ExportPanel
from oledify.ui.worker import ExportTask, FitKey, LoadTask, PreviewTask, WorkerSignals, preview_size

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
DEBOUNCE_MS = 150
PANEL_WIDTH = 300
CENTER = (0.5, 0.5)
PRESET_LABEL_ROLE = Qt.ItemDataRole.UserRole + 1


class SliderRow(QWidget):
    """Title, current value and a horizontal slider."""

    def __init__(self, title: str, minimum: int, maximum: int, value: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setValue(value)
        self.value_label = QLabel(str(value))
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_label.setMinimumWidth(28)
        self.slider.valueChanged.connect(lambda v: self.value_label.setText(str(v)))

        header = QHBoxLayout()
        header.addWidget(QLabel(title))
        header.addStretch()
        header.addWidget(self.value_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header)
        layout.addWidget(self.slider)

    def value(self) -> int:
        return self.slider.value()


def focus_to_center(
    focus: tuple[float, float],
    click: tuple[float, float],
    src_size: tuple[int, int],
    target_size: tuple[int, int],
) -> tuple[float, float]:
    """New crop focus that centres the clicked point of the current crop, clamped to the image.

    Crop geometry only (mirrors how fit_image places a cover crop); no pixels involved.
    """
    src_w, src_h = src_size
    target_w, target_h = target_size
    scale = max(target_w / src_w, target_h / src_h)

    def axis(f: float, c: float, scaled: float, target: int) -> float:
        slack = scaled - target
        if slack < 1:
            return f  # nothing to crop along this axis
        clicked = f * slack + c * target
        return min(1.0, max(0.0, (clicked - target / 2) / slack))

    return (
        axis(focus[0], click[0], src_w * scale, target_w),
        axis(focus[1], click[1], src_h * scale, target_h),
    )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"OLEDify {__version__}")
        self.setAcceptDrops(True)
        self.resize(1360, 860)

        self._pool = QThreadPool.globalInstance()
        self._signals = WorkerSignals(self)
        self._signals.loaded.connect(self._on_loaded)
        self._signals.load_failed.connect(self._on_load_failed)
        self._signals.rendered.connect(self._on_rendered)
        self._signals.render_failed.connect(self._on_render_failed)
        self._signals.export_finished.connect(self._on_export_finished)
        self._signals.export_failed.connect(self._on_export_failed)

        self._load_id = 0
        self._render_id = 0
        self._source: np.ndarray | None = None
        self._source_path: str | None = None
        self._focus = CENTER
        self._fitted_key: FitKey | None = None
        self._fitted: np.ndarray | None = None
        self._last_export_dir: str | None = None

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._request_render)

        self.compare = CompareView()
        self.compare.focus_clicked.connect(self._on_focus_clicked)

        # --- right panel ---
        open_button = QPushButton("Open image…")
        open_button.clicked.connect(self._choose_file)

        preview_box = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_box)
        self.preview_combo = QComboBox()
        self.preview_combo.currentIndexChanged.connect(self._on_preview_target_changed)
        self.upscale_label = QLabel()
        self.upscale_label.setWordWrap(True)
        self.upscale_label.setStyleSheet("color: #f5c518;")
        self.upscale_label.hide()
        self.focus_hint = QLabel("Click the OLED side to set the crop focus.")
        self.focus_hint.setWordWrap(True)
        self.focus_hint.setEnabled(False)
        preview_layout.addWidget(QLabel("Preview size"))
        preview_layout.addWidget(self.preview_combo)
        preview_layout.addWidget(self.upscale_label)
        preview_layout.addWidget(self.focus_hint)

        self.threshold = SliderRow("Black threshold", 0, 64, 16)
        self.falloff = SliderRow("Falloff", 0, 64, 24)
        for row in (self.threshold, self.falloff):
            row.slider.valueChanged.connect(self._debounce.start)
        crush_box = QGroupBox("True black")
        crush_layout = QVBoxLayout(crush_box)
        crush_layout.addWidget(self.threshold)
        crush_layout.addWidget(self.falloff)

        self.export_panel = ExportPanel()
        self.export_panel.targets_changed.connect(self._rebuild_preview_combo)
        self.export_panel.mode_changed.connect(self._on_preview_target_changed)
        self.export_panel.export_requested.connect(self._on_export_requested)

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.addWidget(open_button)
        panel_layout.addWidget(preview_box)
        panel_layout.addWidget(crush_box)
        panel_layout.addWidget(self.export_panel)
        panel_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(PANEL_WIDTH)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self.compare, stretch=1)
        layout.addWidget(scroll)
        self.setCentralWidget(central)

        # --- status bar ---
        self.size_label = QLabel("No image")
        self.black_label = QLabel("True black: –")
        self.time_label = QLabel("")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # busy indicator: process_file reports no per-target progress
        self.progress.setMaximumWidth(160)
        self.progress.hide()
        self.export_label = QLabel()
        self.export_label.hide()
        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.clicked.connect(self._open_export_folder)
        self.open_folder_button.hide()
        status = self.statusBar()
        status.addWidget(self.progress)
        status.addWidget(self.export_label)
        status.addWidget(self.open_folder_button)
        status.addPermanentWidget(self.size_label)
        status.addPermanentWidget(self.black_label)
        status.addPermanentWidget(self.time_label)

        self._rebuild_preview_combo()

    # --- opening files ---------------------------------------------------

    def _choose_file(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in IMAGE_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Open image", "", f"Images ({patterns})")
        if path:
            self.open_image(path)

    def open_image(self, path: str | Path) -> None:
        self._load_id += 1
        self._render_id += 1  # anything still rendering belongs to the previous image
        self.statusBar().showMessage(f"Loading {Path(path).name}…")
        self._pool.start(LoadTask(self._load_id, path, self._signals))

    @staticmethod
    def _image_path_from(event: QDragEnterEvent | QDropEvent) -> str | None:
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(IMAGE_EXTENSIONS):
                return url.toLocalFile()
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._image_path_from(event):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        path = self._image_path_from(event)
        if path:
            event.acceptProposedAction()
            self.open_image(path)

    def _on_loaded(self, request_id: int, path: str, source: np.ndarray) -> None:
        if request_id != self._load_id:
            return  # stale: another image was opened since
        self._source = source
        self._source_path = path
        self._focus = CENTER
        self._fitted_key = self._fitted = None
        src_h, src_w = source.shape[:2]
        self.size_label.setText(f"{Path(path).name} · {src_w}×{src_h}")
        self.black_label.setText("True black: –")
        self.statusBar().clearMessage()
        self.export_panel.set_source(path)
        self._update_preview_state()
        self._request_render()

    def _on_load_failed(self, request_id: int, path: str, message: str) -> None:
        if request_id != self._load_id:
            return
        self.statusBar().clearMessage()
        QMessageBox.warning(self, "Could not open image", f"{Path(path).name}\n\n{message}")

    # --- preview ---------------------------------------------------------

    def _rebuild_preview_combo(self) -> None:
        """List the checked output sizes; keep the current choice (by name) if still checked."""
        current = self.preview_combo.currentData()
        current_label = self.preview_combo.currentData(PRESET_LABEL_ROLE)
        self.preview_combo.blockSignals(True)
        self.preview_combo.clear()
        for label, (w, h) in self.export_panel.targets():
            self.preview_combo.addItem(f"{label} · {w}×{h}", (w, h))
            self.preview_combo.setItemData(self.preview_combo.count() - 1, label, PRESET_LABEL_ROLE)
        if self.preview_combo.count() == 0:
            self.preview_combo.addItem("Source aspect (no size selected)", None)
        index = self.preview_combo.findData(current_label, PRESET_LABEL_ROLE) if current_label else -1
        self.preview_combo.setCurrentIndex(max(0, index))
        self.preview_combo.blockSignals(False)
        if self.preview_combo.currentData() != current:
            self._on_preview_target_changed()

    def _preview_target(self) -> tuple[int, int] | None:
        return self.preview_combo.currentData()

    def _src_size(self) -> tuple[int, int] | None:
        if self._source is None:
            return None
        return self._source.shape[1], self._source.shape[0]

    def _focus_active(self) -> bool:
        return self.export_panel.mode() == "crop" and self._preview_target() is not None

    def _fit_key(self) -> FitKey | None:
        src_size = self._src_size()
        if src_size is None:
            return None
        target = self._preview_target()
        if target is None:
            return (*preview_size(*src_size), "crop", CENTER)  # same aspect: a plain resize
        mode = self.export_panel.mode()
        return (*preview_size(*target), mode, self._focus if mode == "crop" else CENTER)

    def _update_preview_state(self) -> None:
        focus_active = self._focus_active()
        self.compare.set_focus_enabled(focus_active)
        self.focus_hint.setVisible(focus_active)

        src_size, target = self._src_size(), self._preview_target()
        if src_size is None or target is None:
            self.upscale_label.hide()
            return
        if is_upscale(*src_size, *target, self.export_panel.mode()):
            self.upscale_label.setText(
                f"Upscaled: the {src_size[0]}×{src_size[1]} source is enlarged to fill "
                f"{target[0]}×{target[1]}, so it may look soft."
            )
            self.upscale_label.show()
        else:
            self.upscale_label.hide()

    def _on_preview_target_changed(self) -> None:
        self._update_preview_state()
        self._request_render()

    def _on_focus_clicked(self, x: float, y: float) -> None:
        src_size, target = self._src_size(), self._preview_target()
        if src_size is None or target is None or not self._focus_active():
            return
        self._focus = focus_to_center(self._focus, (x, y), src_size, target)
        self._request_render()

    def _request_render(self) -> None:
        key = self._fit_key()
        if key is None or self._source is None:
            return
        fitted = self._fitted if key == self._fitted_key else None
        self._render_id += 1
        task = PreviewTask(
            self._render_id, self._source, key, fitted, self.threshold.value(), self.falloff.value(), self._signals
        )
        self._pool.start(task)

    def _on_rendered(
        self,
        request_id: int,
        fit_key: FitKey,
        fitted: np.ndarray,
        original: QImage,
        processed: QImage,
        percent: float,
        elapsed_ms: float,
    ) -> None:
        if request_id != self._render_id:
            return  # stale: a newer render was requested since
        self._fitted_key, self._fitted = fit_key, fitted
        self.compare.set_images(original, processed)
        self.black_label.setText(f"True black: {percent:.1f}%")
        self.time_label.setText(f"Preview {elapsed_ms:.0f} ms")

    def _on_render_failed(self, request_id: int, message: str) -> None:
        if request_id == self._render_id:
            self.statusBar().showMessage(f"Preview failed: {message}", 5000)

    # --- export ----------------------------------------------------------

    def _on_export_requested(self, settings: Settings, targets: list[tuple[int, int]], out_dir: str) -> None:
        if self._source_path is None:
            return
        settings = replace(
            settings,
            threshold=self.threshold.value(),
            falloff=self.falloff.value(),
            focus=self._focus,
        )
        self.export_panel.set_busy(True)
        # No showMessage() here: a status message would hide the progress bar and label.
        self.statusBar().clearMessage()
        self.open_folder_button.hide()
        self.export_label.setText(f"Exporting {len(targets)} file(s)…")
        self.export_label.show()
        self.progress.show()
        self._pool.start(ExportTask(self._source_path, out_dir, targets, settings, self._signals))

    def _on_export_finished(self, out_dir: str, results: list) -> None:
        self.export_panel.set_busy(False)
        self.progress.hide()
        self._last_export_dir = out_dir
        self.export_label.setText(f"Exported {len(results)} file(s) to {out_dir}")
        self.export_label.show()
        self.open_folder_button.show()

    def _on_export_failed(self, message: str) -> None:
        self.export_panel.set_busy(False)
        self.progress.hide()
        self.export_label.hide()
        QMessageBox.critical(self, "Export failed", message)

    def _open_export_folder(self) -> None:
        if not self._last_export_dir:
            return
        if hasattr(os, "startfile"):
            os.startfile(self._last_export_dir)  # Windows
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_export_dir))
