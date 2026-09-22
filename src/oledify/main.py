"""Application entry point."""

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from oledify import __version__

# Running from source: <repo>/assets. A packaged build must ship this folder alongside.
ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
APP_USER_MODEL_ID = "rooman-dev.oledify"


def app_icon() -> QIcon:
    """The app icon; the .ico carries 16-256 px sizes for crisp taskbar/title-bar rendering."""
    for name in ("icon.ico", "icon.png"):
        path = ASSETS_DIR / name
        if path.exists():
            return QIcon(str(path))
    return QIcon()


def dark_palette() -> QPalette:
    palette = QPalette()
    base = QColor(18, 18, 18)
    window = QColor(30, 30, 30)
    text = QColor(225, 225, 225)
    disabled = QColor(120, 120, 120)
    highlight = QColor(64, 128, 255)

    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, window)
    palette.setColor(QPalette.ColorRole.ToolTipBase, window)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
    palette.setColor(QPalette.ColorRole.Link, highlight)
    palette.setColor(QPalette.ColorRole.Highlight, highlight)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.PlaceholderText, disabled)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return palette


def set_app_user_model_id() -> None:
    """Windows: give the process its own identity so the taskbar shows our icon, not Python's."""
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:  # not Windows, or the call is unavailable: harmless either way
        pass


def main() -> int:
    """Launch OLEDify."""
    set_app_user_model_id()
    # Qt 6 always scales for high DPI; this passes fractional factors (125%, 150%) through unrounded.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("OLEDify")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setWindowIcon(app_icon())

    from oledify.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
