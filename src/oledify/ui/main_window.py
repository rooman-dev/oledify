"""Main application window: load images, tune the black crush live, export presets in batch."""

import errno
import os
import threading
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError
from PySide6.QtCore import QByteArray, QSettings, QSize, QThreadPool, QTimer, QUrl, Qt
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QImage,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from oledify import __version__
from oledify.core.pipeline import Cancelled, Settings
from oledify.core.resize import is_upscale
from oledify.ui.compare_view import DEFAULT_PLACEHOLDER, CompareView
from oledify.ui.export_panel import ExportPanel
from oledify.ui.worker import (
    THUMB_MAX_SIDE,
    BatchExportTask,
    FitKey,
    LoadTask,
    PreviewTask,
    ThumbnailTask,
    WorkerSignals,
    preview_size,
)

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
DEBOUNCE_MS = 150
PANEL_WIDTH = 300
LIST_WIDTH = 210
CENTER = (0.5, 0.5)
PRESET_LABEL_ROLE = Qt.ItemDataRole.UserRole + 1
PATH_ROLE = Qt.ItemDataRole.UserRole
MAX_NAMES_IN_SUMMARY = 3
SETTINGS_ORG, SETTINGS_APP = "rooman-dev", "OLEDify"
REPO_URL = "https://github.com/rooman-dev/oledify"
DEFAULT_THRESHOLD, DEFAULT_FALLOFF = 16, 24
UNREADABLE_PLACEHOLDER = "Can't read this file"

# Windows error codes for a full disk (ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL)
_WIN_DISK_FULL = (39, 112)


def friendly_message(exc: BaseException) -> str:
    """Plain-language explanation of a load/save failure."""
    if isinstance(exc, UnidentifiedImageError):  # subclass of OSError: check first
        return "This file isn't an image OLEDify can read, or it's damaged."
    if isinstance(exc, Image.DecompressionBombError):
        return "This image is too large to open safely."
    if isinstance(exc, MemoryError):
        return "There isn't enough memory for this image. Close other programs or export fewer sizes at once."
    if isinstance(exc, PermissionError):
        return (
            "OLEDify isn't allowed to access this file or folder. Check it isn't read-only or open "
            "in another program, or choose a different output folder."
        )
    if isinstance(exc, FileNotFoundError):
        return "The file or folder no longer exists. It may have been moved, renamed or deleted."
    if isinstance(exc, (FileExistsError, NotADirectoryError)):
        return "The output folder can't be used because a file with the same name is in the way."
    if isinstance(exc, OSError) and (
        exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) in _WIN_DISK_FULL
    ):
        return "The disk is full. Free up some space or choose a different output folder."
    return "Something went wrong while processing this image."


def error_details(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("About OLEDify")
        icon = QLabel()
        icon.setPixmap(QApplication.windowIcon().pixmap(96, 96))
        text = QLabel(
            f"<h2>OLEDify {__version__}</h2>"
            "<p>Converts wallpapers for OLED and AMOLED screens:<br>"
            "true-black crush and resize to common display sizes.</p>"
            "<p>Runs entirely on your computer. No AI, no network access.</p>"
            "<p>Free and open source under the MIT License.<br>"
            f'<a href="{REPO_URL}">github.com/rooman-dev/oledify</a></p>'
        )
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setOpenExternalLinks(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)

        row = QHBoxLayout()
        row.addWidget(icon, alignment=Qt.AlignmentFlag.AlignTop)
        row.addSpacing(12)
        row.addWidget(text)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(buttons)


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


def normalize_path(path: str | Path) -> str:
    return os.path.normpath(os.path.abspath(path))


def is_image_file(path: str | Path) -> bool:
    return str(path).lower().endswith(IMAGE_EXTENSIONS)


def expand_paths(paths: list[str | Path]) -> list[str]:
    """Supported image files from paths; folders contribute their direct children, sorted by name."""
    files: list[str] = []
    for path in map(Path, paths):
        if path.is_dir():
            children = sorted((p for p in path.iterdir() if p.is_file() and is_image_file(p)), key=lambda p: p.name.lower())
            files.extend(normalize_path(p) for p in children)
        elif path.is_file() and is_image_file(path):
            files.append(normalize_path(path))
    return list(dict.fromkeys(files))


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


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.setWindowTitle(f"OLEDify {__version__}")
        self.setAcceptDrops(True)
        self.resize(1500, 880)
        self._settings = settings if settings is not None else QSettings(SETTINGS_ORG, SETTINGS_APP)
        self._last_open_dir = ""

        self._pool = QThreadPool.globalInstance()
        # Deliberately unparented: running tasks hold a reference, so the signal hub
        # outlives the window and late emits from workers can't hit a deleted object.
        self._signals = WorkerSignals()
        self._signals.loaded.connect(self._on_loaded)
        self._signals.load_failed.connect(self._on_load_failed)
        self._signals.rendered.connect(self._on_rendered)
        self._signals.render_failed.connect(self._on_render_failed)
        self._signals.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._signals.thumbnail_failed.connect(self._on_thumbnail_failed)
        self._signals.export_progress.connect(self._on_export_progress)
        self._signals.export_finished.connect(self._on_export_finished)
        self._signals.export_failed.connect(self._on_export_failed)

        self._load_id = 0
        self._render_id = 0
        self._items: dict[str, QListWidgetItem] = {}
        self._focus_by_path: dict[str, tuple[float, float]] = {}
        self._source: np.ndarray | None = None
        self._source_path: str | None = None
        self._fitted_key: FitKey | None = None
        self._fitted: np.ndarray | None = None
        self._cancel_event: threading.Event | None = None
        self._last_export_dir: str | None = None

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._request_render)

        # --- left: file list ---
        placeholder = QPixmap(THUMB_MAX_SIDE, THUMB_MAX_SIDE * 9 // 16)
        placeholder.fill(QColor(45, 45, 45))
        self._placeholder_icon = QIcon(placeholder)
        self._broken_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)

        self.file_list = QListWidget()
        self.file_list.setViewMode(QListView.ViewMode.IconMode)
        self.file_list.setIconSize(QSize(THUMB_MAX_SIDE, THUMB_MAX_SIDE * 9 // 16))
        self.file_list.setGridSize(QSize(THUMB_MAX_SIDE + 16, THUMB_MAX_SIDE * 9 // 16 + 40))
        self.file_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.file_list.setMovement(QListView.Movement.Static)
        self.file_list.setWordWrap(True)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.currentItemChanged.connect(self._on_current_item_changed)

        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(self.remove_selected)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.clear_files)
        list_buttons = QHBoxLayout()
        list_buttons.addWidget(remove_button)
        list_buttons.addWidget(clear_button)

        left = QWidget()
        left.setFixedWidth(LIST_WIDTH)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Images"))
        left_layout.addWidget(self.file_list)
        left_layout.addLayout(list_buttons)

        # --- centre: compare view ---
        self.compare = CompareView()
        self.compare.focus_clicked.connect(self._on_focus_clicked)

        # --- right panel ---
        open_button = QPushButton("Open images…")
        open_button.clicked.connect(self._choose_files)

        preview_box = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_box)
        self.preview_combo = QComboBox()
        self.preview_combo.currentIndexChanged.connect(self._on_preview_target_changed)
        self.upscale_label = QLabel()
        self.upscale_label.setWordWrap(True)
        self.upscale_label.setStyleSheet("color: #f5c518;")
        self.upscale_label.hide()
        self.focus_hint = QLabel("Click the OLED side to set this image's crop focus.")
        self.focus_hint.setWordWrap(True)
        self.focus_hint.setEnabled(False)
        preview_layout.addWidget(QLabel("Preview size"))
        preview_layout.addWidget(self.preview_combo)
        preview_layout.addWidget(self.upscale_label)
        preview_layout.addWidget(self.focus_hint)

        self.threshold = SliderRow("Black threshold", 0, 64, DEFAULT_THRESHOLD)
        self.falloff = SliderRow("Falloff", 0, 64, DEFAULT_FALLOFF)
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

        reset_button = QPushButton("Reset to defaults")
        reset_button.clicked.connect(self.reset_to_defaults)

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.addWidget(open_button)
        panel_layout.addWidget(preview_box)
        panel_layout.addWidget(crush_box)
        panel_layout.addWidget(self.export_panel)
        panel_layout.addSpacing(8)
        panel_layout.addWidget(reset_button)
        panel_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(PANEL_WIDTH)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(left)
        layout.addWidget(self.compare, stretch=1)
        layout.addWidget(scroll)
        self.setCentralWidget(central)

        # --- status bar ---
        self.size_label = QLabel("No image")
        self.black_label = QLabel("True black: –")
        self.time_label = QLabel("")
        self.progress = QProgressBar()
        self.progress.setFormat("%v / %m")
        self.progress.setMaximumWidth(180)
        self.progress.hide()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel_export)
        self.cancel_button.hide()
        self.export_label = QLabel()
        self.export_label.hide()
        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.clicked.connect(self._open_export_folder)
        self.open_folder_button.hide()
        # Note: never use statusBar().showMessage() while these are visible; it hides them.
        status = self.statusBar()
        status.addWidget(self.progress)
        status.addWidget(self.cancel_button)
        status.addWidget(self.export_label)
        status.addWidget(self.open_folder_button)
        status.addPermanentWidget(self.size_label)
        status.addPermanentWidget(self.black_label)
        status.addPermanentWidget(self.time_label)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("&About OLEDify", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

        self._rebuild_preview_combo()
        self._restore_settings()

    # --- settings --------------------------------------------------------

    def _setting(self, key: str, default, type_):
        """Typed QSettings read that falls back to default on missing or corrupt values.

        Converts the raw value itself: QSettings' own type= conversion silently turns
        garbage into 0/False instead of failing.
        """
        if not self._settings.contains(key):
            return default
        raw = self._settings.value(key)
        if type_ is bool:
            if isinstance(raw, bool):
                return raw
            text = str(raw).strip().lower()
            return {"true": True, "1": True, "false": False, "0": False}.get(text, default)
        if type_ is str and isinstance(raw, list):  # INI files may split comma lists
            return ",".join(map(str, raw))
        try:
            return type_(raw)
        except (TypeError, ValueError):
            return default

    def _restore_settings(self) -> None:
        self.threshold.slider.setValue(self._setting("crush/threshold", DEFAULT_THRESHOLD, int))
        self.falloff.slider.setValue(self._setting("crush/falloff", DEFAULT_FALLOFF, int))

        state: dict = {}
        if self._settings.contains("export/presets"):
            names = self._setting("export/presets", "", str)
            state["presets"] = [name for name in names.split(",") if name]
        for key, type_ in (
            ("custom_enabled", bool),
            ("custom_w", int),
            ("custom_h", int),
            ("mode", str),
            ("format", str),
            ("quality", int),
            ("deband", bool),
            ("folder", str),
        ):
            value = self._setting(f"export/{key}", None, type_)
            if value is not None:
                state[key] = value
        self.export_panel.apply_state(state)

        self._last_open_dir = self._setting("paths/last_open_dir", "", str)
        self.compare.split = self._setting("view/split", 0.5, float)
        geometry = self._settings.value("window/geometry")
        if isinstance(geometry, QByteArray) and not geometry.isEmpty():
            self.restoreGeometry(geometry)

    def save_settings(self) -> None:
        s = self._settings
        s.setValue("crush/threshold", self.threshold.value())
        s.setValue("crush/falloff", self.falloff.value())
        state = self.export_panel.state()
        s.setValue("export/presets", ",".join(state.pop("presets")))
        for key, value in state.items():
            s.setValue(f"export/{key}", value)
        s.setValue("paths/last_open_dir", self._last_open_dir)
        s.setValue("view/split", self.compare.split)
        s.setValue("window/geometry", self.saveGeometry())
        s.sync()

    def reset_to_defaults(self) -> None:
        """Reset processing, export and view options. Window size and last open folder are kept."""
        self.threshold.slider.setValue(DEFAULT_THRESHOLD)
        self.falloff.slider.setValue(DEFAULT_FALLOFF)
        self.export_panel.reset_defaults()
        self.compare.split = 0.5
        self.save_settings()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.save_settings()
        super().closeEvent(event)

    # --- dialogs ---------------------------------------------------------

    def show_about(self) -> None:
        AboutDialog(self).exec()

    def show_error(self, title: str, text: str, details: str) -> None:
        """Friendly message, with the raw error text behind "Show Details…"."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(text)
        box.setDetailedText(details)
        box.exec()

    # --- file list -------------------------------------------------------

    def paths(self) -> list[str]:
        return [self.file_list.item(row).data(PATH_ROLE) for row in range(self.file_list.count())]

    def _choose_files(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in IMAGE_EXTENSIONS)
        paths, _ = QFileDialog.getOpenFileNames(self, "Open images", self._last_open_dir, f"Images ({patterns})")
        if paths:
            self._last_open_dir = str(Path(paths[0]).parent)
            self.add_files(paths)

    def add_files(self, paths: list[str | Path]) -> list[str]:
        """Add image files (and supported files inside folders); returns the newly added paths."""
        added = []
        for path in expand_paths(paths):
            if path in self._items:
                continue
            item = QListWidgetItem(self._placeholder_icon, Path(path).name)
            item.setData(PATH_ROLE, path)
            item.setToolTip(path)
            self.file_list.addItem(item)
            self._items[path] = item
            added.append(path)
            self._pool.start(ThumbnailTask(path, self._signals))
        self._on_files_changed()
        if added and self.file_list.currentItem() is None:
            self.file_list.setCurrentItem(self._items[added[0]])
        return added

    def remove_selected(self) -> None:
        selected = self.file_list.selectedItems()
        if not selected:
            return
        previous = self.file_list.currentItem()
        self.file_list.blockSignals(True)
        for item in selected:
            path = item.data(PATH_ROLE)
            self._items.pop(path, None)
            self._focus_by_path.pop(path, None)
            self.file_list.takeItem(self.file_list.row(item))
        self.file_list.blockSignals(False)
        current = self.file_list.currentItem()
        if current is not previous or previous in selected:
            self._on_current_item_changed(current, None)
        self._on_files_changed()

    def clear_files(self) -> None:
        self.file_list.blockSignals(True)
        self.file_list.clear()
        self.file_list.blockSignals(False)
        self._items.clear()
        self._focus_by_path.clear()
        self._on_current_item_changed(None, None)
        self._on_files_changed()

    def _on_files_changed(self) -> None:
        self.export_panel.set_files(self.paths())

    def _on_current_item_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            self._clear_preview()
        else:
            self._load_preview(current.data(PATH_ROLE))

    def _on_thumbnail_ready(self, path: str, image: QImage) -> None:
        item = self._items.get(path)
        if item is not None:  # the file may have been removed meanwhile
            item.setIcon(QIcon(QPixmap.fromImage(image)))

    def _on_thumbnail_failed(self, path: str, exc: BaseException) -> None:
        item = self._items.get(path)
        if item is not None:
            item.setIcon(self._broken_icon)
            item.setText(f"{Path(path).name}\n(unreadable)")
            item.setToolTip(f"{path}\n{friendly_message(exc)}")

    @staticmethod
    def _dropped_paths(event: QDragEnterEvent | QDropEvent) -> list[str]:
        return [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile() and (os.path.isdir(url.toLocalFile()) or is_image_file(url.toLocalFile()))
        ]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._dropped_paths(event):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = self._dropped_paths(event)
        if paths:
            event.acceptProposedAction()
            first = Path(paths[0])
            self._last_open_dir = str(first if first.is_dir() else first.parent)
            self.add_files(paths)

    # --- preview source --------------------------------------------------

    def _load_preview(self, path: str) -> None:
        self._load_id += 1
        self._render_id += 1  # anything still rendering belongs to the previous image
        self._pool.start(LoadTask(self._load_id, path, self._signals))

    def _clear_preview(self) -> None:
        self._load_id += 1
        self._render_id += 1
        self._source = None
        self._source_path = None
        self._fitted_key = self._fitted = None
        self.compare.set_placeholder(DEFAULT_PLACEHOLDER)
        self.compare.set_original(None)
        self.size_label.setText("No image")
        self.black_label.setText("True black: –")
        self.time_label.setText("")
        self._update_preview_state()

    def _on_loaded(self, request_id: int, path: str, source: np.ndarray) -> None:
        if request_id != self._load_id:
            return  # stale: another image was selected since
        self._source = source
        self._source_path = path
        self._fitted_key = self._fitted = None
        src_h, src_w = source.shape[:2]
        self.size_label.setText(f"{Path(path).name} · {src_w}×{src_h}")
        self.black_label.setText("True black: –")
        self._update_preview_state()
        self._request_render()

    def _on_load_failed(self, request_id: int, path: str, exc: BaseException) -> None:
        if request_id != self._load_id:
            return
        self._source = None
        self._source_path = None
        self.compare.set_placeholder(f"{UNREADABLE_PLACEHOLDER}\n\n{friendly_message(exc)}")
        self.compare.set_original(None)
        self.size_label.setText(f"{Path(path).name} · unreadable")
        self.black_label.setText("True black: –")
        self.time_label.setText("")
        self._update_preview_state()

    # --- preview ---------------------------------------------------------

    @property
    def _focus(self) -> tuple[float, float]:
        return self._focus_by_path.get(self._source_path, CENTER) if self._source_path else CENTER

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
        return (
            self._source is not None
            and self.export_panel.mode() == "crop"
            and self._preview_target() is not None
        )

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
        if src_size is None or target is None or self._source_path is None or not self._focus_active():
            return
        self._focus_by_path[self._source_path] = focus_to_center(self._focus, (x, y), src_size, target)
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
            self.time_label.setText(f"Preview failed: {message}")

    # --- export ----------------------------------------------------------

    def _on_export_requested(self, settings: Settings, targets: list[tuple[int, int]], out_dir: str) -> None:
        paths = self.paths()
        if not paths or not targets:
            return
        settings = replace(settings, threshold=self.threshold.value(), falloff=self.falloff.value(), focus=CENTER)
        overrides = {
            path: replace(settings, focus=focus)
            for path, focus in self._focus_by_path.items()
            if path in self._items and focus != CENTER
        }
        self._cancel_event = threading.Event()

        self.export_panel.set_busy(True)
        self.open_folder_button.hide()
        self.export_label.setText(f"Exporting {plural(len(paths), 'image')}…")
        self.export_label.setToolTip("")
        self.export_label.show()
        self.progress.setRange(0, len(paths) * len(targets))
        self.progress.setValue(0)
        self.progress.show()
        self.cancel_button.setText("Cancel")
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        task = BatchExportTask(paths, out_dir, targets, settings, overrides, self._cancel_event, self._signals)
        self._pool.start(task)

    def _on_export_progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(done)

    def _cancel_export(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
            self.cancel_button.setText("Cancelling…")
            self.cancel_button.setEnabled(False)

    def _end_export(self) -> None:
        self.export_panel.set_busy(False)
        self.progress.hide()
        self.cancel_button.hide()
        self._cancel_event = None

    def _on_export_finished(self, out_dir: str, results: dict, cancelled: bool) -> None:
        self._end_export()
        exported = [src for src, result in results.items() if isinstance(result, list)]
        skipped = [src for src, result in results.items() if isinstance(result, Cancelled)]
        failed = [
            (src, result)
            for src, result in results.items()
            if isinstance(result, Exception) and not isinstance(result, Cancelled)
        ]

        summary = f"{plural(len(exported), 'file')} exported, {len(failed)} failed"
        if failed:
            names = [src.name for src, _ in failed]
            shown = ", ".join(names[:MAX_NAMES_IN_SUMMARY])
            more = len(names) - MAX_NAMES_IN_SUMMARY
            summary += f": {shown}" + (f" and {more} more" if more > 0 else "")
        if cancelled:
            summary += f" · cancelled, {len(skipped)} skipped"
        self.export_label.setText(summary)
        self.export_label.setToolTip("\n".join(f"{src.name}: {friendly_message(exc)}" for src, exc in failed))
        self.export_label.show()

        if exported:
            self._last_export_dir = out_dir
            self.open_folder_button.show()
        if failed:
            friendly = "\n".join(f"• {src.name}: {friendly_message(exc)}" for src, exc in failed)
            details = "\n".join(f"{src}\n    {error_details(exc)}" for src, exc in failed)
            self.show_error("Some images failed", f"{summary}\n\n{friendly}", details)

    def _on_export_failed(self, exc: BaseException) -> None:
        self._end_export()
        self.export_label.hide()
        self.show_error("Export failed", friendly_message(exc), error_details(exc))

    def _open_export_folder(self) -> None:
        if not self._last_export_dir:
            return
        if hasattr(os, "startfile"):
            os.startfile(self._last_export_dir)  # Windows
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_export_dir))
