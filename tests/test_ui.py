"""UI tests. Headless (offscreen platform, see conftest.py); images are kept small for speed."""

from pathlib import Path

import numpy as np
import pytest

from oledify.core.export import deband, output_name
from oledify.core.oled import crush_blacks, true_black_percent
from oledify.ui.worker import to_qimage

RENDER_TIMEOUT_MS = 15000
EXPORT_TIMEOUT_MS = 30000


def load(window, qtbot, path):
    """Add one file and wait for its first preview render."""
    with qtbot.waitSignal(window._signals.rendered, timeout=RENDER_TIMEOUT_MS):
        window.add_files([path])
    return path


def settle(window, qtbot):
    """Wait until no render is outstanding."""
    qtbot.waitUntil(lambda: window._fitted_key == window._fit_key(), timeout=RENDER_TIMEOUT_MS)


def test_starts_empty_with_export_disabled(window):
    assert window.file_list.count() == 0
    assert not window.export_panel._export_button.isEnabled()
    assert window.size_label.text() == "No image"


def test_loading_image_enables_export_and_shows_true_black(window, qtbot, make_file):
    path = load(window, qtbot, make_file())
    assert window.export_panel._export_button.isEnabled()
    assert Path(path).name in window.size_label.text()
    assert "640×360" in window.size_label.text()

    text = window.black_label.text()
    assert text.startswith("True black: ") and text.endswith("%")
    assert float(text.removeprefix("True black: ").removesuffix("%")) > 0


def test_slider_burst_produces_one_render(window, qtbot, make_file):
    load(window, qtbot, make_file())
    settle(window, qtbot)

    renders = []
    window._signals.rendered.connect(lambda *a: renders.append(a[0]))
    # Widen the debounce so a slow machine can't finish the burst after it fires;
    # what matters is that the 10 changes coalesce into one render, not the timing.
    window._debounce.setInterval(1000)
    with qtbot.waitSignal(window._signals.rendered, timeout=RENDER_TIMEOUT_MS):
        for value in range(10, 30, 2):  # 10 changes inside the debounce window
            window.threshold.slider.setValue(value)
    qtbot.wait(500)

    assert len(renders) == 1
    assert window.threshold.value() == 28


def test_stale_render_is_discarded(window, qtbot, make_file):
    load(window, qtbot, make_file())
    settle(window, qtbot)
    current_black = window.black_label.text()
    current_key = window._fitted_key

    stale = to_qimage(np.zeros((4, 4, 3), dtype=np.uint8))
    window._on_rendered(window._render_id - 1, ("stale",), np.zeros((4, 4, 3), np.uint8), stale, stale, 99.9, 1.0)

    assert window.black_label.text() == current_black
    assert window._fitted_key == current_key


def test_newest_render_wins_after_burst(window, qtbot, make_file):
    load(window, qtbot, make_file())
    settle(window, qtbot)

    for threshold in (5, 20, 40, 60):  # queue several renders at once, bypassing the debounce
        window.threshold.slider.setValue(threshold)
        window._request_render()

    # Let every queued render finish (they complete out of order), then flush their
    # queued signals. Waiting a fixed time here is what made this test flaky.
    qtbot.waitUntil(lambda: window._pool.waitForDone(50), timeout=RENDER_TIMEOUT_MS)
    qtbot.wait(200)

    # The status bar must show the last threshold's result, not an earlier, faster one.
    expected = true_black_percent(deband(crush_blacks(window._fitted, threshold=60, falloff=24), seed=0))
    assert window.black_label.text() == f"True black: {expected:.1f}%"
    assert window.time_label.text().startswith("Preview ")


def test_upscale_warning_follows_fit_mode(window, qtbot, make_file):
    load(window, qtbot, make_file("big.png", h=1080, w=1920))
    window.export_panel._preset_checks["4K"].setChecked(False)
    window.export_panel._preset_checks["Phone FHD+"].setChecked(True)
    settle(window, qtbot)
    assert not window.upscale_label.isHidden()  # 1920x1080 cropped to fill 1080x2400

    window.export_panel._mode_combo.setCurrentIndex(1)  # black bars: fits inside, no upscale
    settle(window, qtbot)
    assert window.upscale_label.isHidden()


def test_file_list_add_and_clear(window, qtbot, make_file):
    paths = [make_file(f"w{i}.png") for i in range(3)]
    with qtbot.waitSignal(window._signals.rendered, timeout=RENDER_TIMEOUT_MS):
        window.add_files(paths)
    assert window.file_list.count() == 3
    assert window.export_panel._export_button.text() == "Export 3 images"

    window.clear_files()
    assert window.file_list.count() == 0
    assert window._source is None
    assert not window.export_panel._export_button.isEnabled()


def test_export_writes_expected_files(window, qtbot, make_file, tmp_path):
    paths = [make_file("a.png"), make_file("b.jpg")]
    with qtbot.waitSignal(window._signals.rendered, timeout=RENDER_TIMEOUT_MS):
        window.add_files(paths)

    panel = window.export_panel
    panel._preset_checks["4K"].setChecked(False)
    panel._custom_check.setChecked(True)
    panel._custom_w.setValue(320)
    panel._custom_h.setValue(180)
    out_dir = tmp_path / "out"
    panel.set_output_folder(out_dir)

    with qtbot.waitSignal(window._signals.export_finished, timeout=EXPORT_TIMEOUT_MS):
        panel._export_button.click()

    expected = sorted(output_name(p, 320, 180, "png") for p in paths)
    assert sorted(p.name for p in out_dir.iterdir()) == expected
    assert window.export_label.text().startswith("2 files exported, 0 failed")


def test_export_reports_unreadable_file(window, qtbot, make_file, tmp_path, monkeypatch):
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not an image")
    with qtbot.waitSignal(window._signals.rendered, timeout=RENDER_TIMEOUT_MS):
        window.add_files([make_file("ok.png"), str(bad)])

    errors = []
    monkeypatch.setattr(window, "show_error", lambda *args: errors.append(args))
    panel = window.export_panel
    panel._preset_checks["4K"].setChecked(False)
    panel._custom_check.setChecked(True)
    panel._custom_w.setValue(320)
    panel._custom_h.setValue(180)
    panel.set_output_folder(tmp_path / "out")

    with qtbot.waitSignal(window._signals.export_finished, timeout=EXPORT_TIMEOUT_MS):
        panel._export_button.click()

    assert window.export_label.text().startswith("1 file exported, 1 failed: broken.png")
    assert errors and "isn't an image" in errors[0][1]


@pytest.mark.parametrize("mode_index, expects_focus", [(0, True), (1, False), (2, False)])
def test_focus_picking_only_in_crop_mode(window, qtbot, make_file, mode_index, expects_focus):
    load(window, qtbot, make_file())
    window.export_panel._mode_combo.setCurrentIndex(mode_index)
    settle(window, qtbot)
    assert window.compare._focus_enabled is expects_focus
