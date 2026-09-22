"""Shared fixtures. Qt runs offscreen so the UI tests need no display."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # must be set before Qt is imported

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402


def make_image(h: int, w: int) -> np.ndarray:
    """Horizontal ramp from black, so crushing always produces some true black."""
    x = np.linspace(0, 255, w, dtype=np.float32)
    ramp = np.broadcast_to(x, (h, w))
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[..., 0] = ramp.astype(np.uint8)
    img[..., 1] = ramp.astype(np.uint8) // 2
    img[..., 2] = np.linspace(0, 255, h, dtype=np.float32)[:, None].astype(np.uint8)
    return img


@pytest.fixture
def make_file(tmp_path):
    """Factory writing a test image; returns its path."""

    def _make(name: str = "wall.png", h: int = 360, w: int = 640) -> str:
        path = tmp_path / name
        Image.fromarray(make_image(h, w)).save(path)
        return str(path)

    return _make


@pytest.fixture
def window(qtbot, tmp_path):
    """MainWindow backed by a throwaway INI file, so real user settings are untouched."""
    from PySide6.QtCore import QSettings

    from oledify.ui.main_window import MainWindow

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    win = MainWindow(settings)
    qtbot.addWidget(win)
    return win
