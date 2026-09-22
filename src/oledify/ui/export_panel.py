"""Output sizes, fit mode, format and export controls."""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from oledify.core.pipeline import Settings
from oledify.presets import PRESETS

FIT_MODES = (("Crop", "crop"), ("Black bars", "bars"), ("Blurred fill", "blur"))
FORMATS = ("png", "jpg", "webp")
DEFAULT_PRESETS = ("4K",)
OUTPUT_SUBFOLDER = "OLEDify"
CUSTOM_MIN, CUSTOM_MAX = 16, 16384


class ExportPanel(QWidget):
    """Collects export options and emits export_requested(settings, targets, out_dir).

    The Settings carry the fields owned by this panel (mode, fmt, quality, deband);
    the window fills in threshold, falloff and focus.
    """

    targets_changed = Signal()
    mode_changed = Signal()
    export_requested = Signal(object, object, str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._file_count = 0
        self._busy = False
        self._folder_chosen = False

        # --- output sizes ---
        sizes_box = QGroupBox("Output sizes")
        grid = QGridLayout(sizes_box)
        self._preset_checks: dict[str, QCheckBox] = {}
        for row, (name, (w, h)) in enumerate(PRESETS.items()):
            check = QCheckBox(name)
            check.setChecked(name in DEFAULT_PRESETS)
            check.toggled.connect(self._on_targets_changed)
            dims = QLabel(f"{w}×{h}")
            dims.setEnabled(False)  # dimmed secondary text
            grid.addWidget(check, row, 0)
            grid.addWidget(dims, row, 1)
            self._preset_checks[name] = check

        self._custom_check = QCheckBox("Custom")
        self._custom_check.toggled.connect(self._on_targets_changed)
        self._custom_w = self._make_spin(1920)
        self._custom_h = self._make_spin(1080)
        custom_dims = QHBoxLayout()
        custom_dims.addWidget(self._custom_w)
        custom_dims.addWidget(QLabel("×"))
        custom_dims.addWidget(self._custom_h)
        row = len(PRESETS)
        grid.addWidget(self._custom_check, row, 0)
        grid.addLayout(custom_dims, row, 1)

        # --- options ---
        options_box = QGroupBox("Options")
        options = QGridLayout(options_box)
        self._mode_combo = QComboBox()
        for label, mode in FIT_MODES:
            self._mode_combo.addItem(label, mode)
        self._mode_combo.currentIndexChanged.connect(self.mode_changed)

        self._format_combo = QComboBox()
        self._format_combo.addItems(FORMATS)
        self._format_combo.currentTextChanged.connect(self._on_format_changed)
        self._quality_label = QLabel("Quality")
        self._quality_spin = QSpinBox()
        self._quality_spin.setRange(1, 100)
        self._quality_spin.setValue(95)

        self._deband_check = QCheckBox("Deband dark gradients")
        self._deband_check.setChecked(True)

        options.addWidget(QLabel("Fit"), 0, 0)
        options.addWidget(self._mode_combo, 0, 1)
        options.addWidget(QLabel("Format"), 1, 0)
        options.addWidget(self._format_combo, 1, 1)
        options.addWidget(self._quality_label, 2, 0)
        options.addWidget(self._quality_spin, 2, 1)
        options.addWidget(self._deband_check, 3, 0, 1, 2)

        # --- output folder + export ---
        folder_box = QGroupBox("Output folder")
        folder_row = QHBoxLayout(folder_box)
        self._folder_edit = QLineEdit()
        self._folder_edit.setReadOnly(True)
        self._folder_edit.setPlaceholderText("Next to the source image")
        browse = QToolButton()
        browse.setText("…")
        browse.clicked.connect(self._choose_folder)
        folder_row.addWidget(self._folder_edit)
        folder_row.addWidget(browse)

        self._export_button = QPushButton("Export")
        self._export_button.clicked.connect(self._on_export_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(sizes_box)
        layout.addWidget(options_box)
        layout.addWidget(folder_box)
        layout.addWidget(self._export_button)

        self._on_format_changed(self._format_combo.currentText())
        self._update_export_enabled()

    def _make_spin(self, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(CUSTOM_MIN, CUSTOM_MAX)
        spin.setValue(value)
        spin.valueChanged.connect(self._on_custom_size_changed)
        return spin

    # --- public API ------------------------------------------------------

    def targets(self) -> list[tuple[str, tuple[int, int]]]:
        """Checked output sizes as (label, (width, height)), presets first."""
        checked = [(name, PRESETS[name]) for name, check in self._preset_checks.items() if check.isChecked()]
        if self._custom_check.isChecked():
            checked.append(("Custom", (self._custom_w.value(), self._custom_h.value())))
        return checked

    def mode(self) -> str:
        return self._mode_combo.currentData()

    def output_folder(self) -> str:
        return self._folder_edit.text()

    def set_output_folder(self, folder: str | Path) -> None:
        self._folder_chosen = True
        self._folder_edit.setText(str(folder))

    def set_files(self, paths: list[str]) -> None:
        """Called when the file list changes; defaults the folder to <first file's folder>/OLEDify."""
        self._file_count = len(paths)
        if paths and not self._folder_chosen:
            self._folder_edit.setText(str(Path(paths[0]).parent / OUTPUT_SUBFOLDER))
        self._update_export_enabled()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_export_enabled()

    # --- internals -------------------------------------------------------

    def _update_export_enabled(self) -> None:
        if self._busy:
            self._export_button.setText("Exporting…")
        elif self._file_count > 1:
            self._export_button.setText(f"Export {self._file_count} images")
        else:
            self._export_button.setText("Export")
        ready = self._file_count > 0 and not self._busy and bool(self.targets())
        self._export_button.setEnabled(ready)
        if not self._file_count:
            self._export_button.setToolTip("Open an image first")
        elif not self.targets():
            self._export_button.setToolTip("Select at least one output size")
        else:
            self._export_button.setToolTip("")

    def _on_targets_changed(self) -> None:
        self._update_export_enabled()
        self.targets_changed.emit()

    def _on_custom_size_changed(self) -> None:
        if self._custom_check.isChecked():
            self.targets_changed.emit()

    def _on_format_changed(self, fmt: str) -> None:
        lossy = fmt != "png"
        self._quality_label.setVisible(lossy)
        self._quality_spin.setVisible(lossy)

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder", self._folder_edit.text())
        if folder:
            self.set_output_folder(folder)

    def _on_export_clicked(self) -> None:
        targets = [size for _, size in self.targets()]
        if not targets or not self._folder_edit.text():
            return
        settings = Settings(
            mode=self.mode(),
            deband=self._deband_check.isChecked(),
            fmt=self._format_combo.currentText(),
            quality=self._quality_spin.value(),
        )
        self.export_requested.emit(settings, targets, self._folder_edit.text())
