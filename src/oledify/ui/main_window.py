"""Main application window: open or drop an image and tune the black crush live."""

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from oledify import __version__
from oledify.ui.compare_view import CompareView
from oledify.ui.worker import LoadTask, PreviewTask, WorkerSignals

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
DEBOUNCE_MS = 150
PANEL_WIDTH = 260


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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"OLEDify {__version__}")
        self.setAcceptDrops(True)
        self.resize(1280, 800)

        self._pool = QThreadPool.globalInstance()
        self._signals = WorkerSignals(self)
        self._signals.loaded.connect(self._on_loaded)
        self._signals.load_failed.connect(self._on_load_failed)
        self._signals.rendered.connect(self._on_rendered)
        self._signals.render_failed.connect(self._on_render_failed)
        self._load_id = 0
        self._render_id = 0
        self._preview: np.ndarray | None = None

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._request_render)

        self.compare = CompareView()

        open_button = QPushButton("Open image…")
        open_button.clicked.connect(self._choose_file)
        self.threshold = SliderRow("Black threshold", 0, 64, 16)
        self.falloff = SliderRow("Falloff", 0, 64, 24)
        for row in (self.threshold, self.falloff):
            row.slider.valueChanged.connect(self._debounce.start)

        crush_box = QGroupBox("True black")
        crush_layout = QVBoxLayout(crush_box)
        crush_layout.addWidget(self.threshold)
        crush_layout.addWidget(self.falloff)

        panel = QWidget()
        panel.setFixedWidth(PANEL_WIDTH)
        panel_layout = QVBoxLayout(panel)
        panel_layout.addWidget(open_button)
        panel_layout.addWidget(crush_box)
        panel_layout.addStretch()

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self.compare, stretch=1)
        layout.addWidget(panel)
        self.setCentralWidget(central)

        self.size_label = QLabel("No image")
        self.black_label = QLabel("True black: –")
        self.time_label = QLabel("")
        status = self.statusBar()
        status.addPermanentWidget(self.size_label)
        status.addPermanentWidget(self.black_label)
        status.addPermanentWidget(self.time_label)

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

    # --- worker results --------------------------------------------------

    def _on_loaded(self, request_id: int, path: str, src_size: tuple, preview: np.ndarray, original: QImage) -> None:
        if request_id != self._load_id:
            return  # stale: another image was opened since
        self._preview = preview
        self.compare.set_original(original)
        self.size_label.setText(f"{Path(path).name} · {src_size[0]}×{src_size[1]}")
        self.black_label.setText("True black: –")
        self.statusBar().clearMessage()
        self._request_render()

    def _on_load_failed(self, request_id: int, path: str, message: str) -> None:
        if request_id != self._load_id:
            return
        self.statusBar().clearMessage()
        QMessageBox.warning(self, "Could not open image", f"{Path(path).name}\n\n{message}")

    def _request_render(self) -> None:
        if self._preview is None:
            return
        self._render_id += 1
        task = PreviewTask(self._render_id, self._preview, self.threshold.value(), self.falloff.value(), self._signals)
        self._pool.start(task)

    def _on_rendered(self, request_id: int, image: QImage, percent: float, elapsed_ms: float) -> None:
        if request_id != self._render_id:
            return  # stale: a newer render was requested since
        self.compare.set_processed(image)
        self.black_label.setText(f"True black: {percent:.1f}%")
        self.time_label.setText(f"Preview {elapsed_ms:.0f} ms")

    def _on_render_failed(self, request_id: int, message: str) -> None:
        if request_id == self._render_id:
            self.statusBar().showMessage(f"Preview failed: {message}", 5000)
