import numpy as np
import pytest

from oledify.core.resize import fit_image, is_upscale

MODES = ["crop", "bars", "blur"]


def gradient(h, w):
    """Horizontal red ramp plus vertical green ramp, so every region differs."""
    x = np.linspace(0, 255, w, dtype=np.float32)[None, :]
    y = np.linspace(0, 255, h, dtype=np.float32)[:, None]
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[..., 0] = np.broadcast_to(x, (h, w)).astype(np.uint8)
    img[..., 1] = np.broadcast_to(y, (h, w)).astype(np.uint8)
    img[..., 2] = 128
    return img


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "src, target",
    [
        ((90, 160), (200, 90)),   # landscape -> portrait (h, w) / (target_w, target_h)
        ((160, 90), (160, 90)),   # portrait -> landscape
        ((50, 80), (320, 180)),   # upscale
        ((400, 300), (64, 64)),   # downscale to square
    ],
)
def test_output_shape_exact(mode, src, target):
    tw, th = target
    out = fit_image(gradient(*src), tw, th, mode=mode)
    assert out.shape == (th, tw, 3)
    assert out.dtype == np.uint8


def test_bars_have_pure_black_borders():
    img = np.full((100, 200, 3), 200, dtype=np.uint8)  # landscape 200x100
    out = fit_image(img, 100, 200, mode="bars")        # portrait target -> 100x50 content
    top, content, bottom = out[:75], out[75:125], out[125:]
    assert not top.any()
    assert not bottom.any()
    assert (content == 200).all()


def test_blur_background_is_dark_not_black():
    img = np.full((100, 200, 3), 200, dtype=np.uint8)
    out = fit_image(img, 100, 200, mode="blur")
    border = out[:60]
    assert border.any()
    assert border.max() <= 200 * 0.4 + 1
    assert (out[90:110] == 200).all()


def test_crop_focus_changes_result():
    img = gradient(100, 400)
    a = fit_image(img, 100, 100, mode="crop", focus=(0.0, 0.0))
    b = fit_image(img, 100, 100, mode="crop", focus=(1.0, 1.0))
    assert not np.array_equal(a, b)
    assert a[..., 0].mean() < b[..., 0].mean()  # left crop is less red


def test_same_size_crop_returns_equal_array():
    img = gradient(60, 80)
    out = fit_image(img, 80, 60, mode="crop")
    np.testing.assert_array_equal(out, img)
    assert out is not img


@pytest.mark.parametrize(
    "src, target, mode, expected",
    [
        ((1920, 1080), (3840, 2160), "crop", True),
        ((1920, 1080), (3840, 2160), "bars", True),
        ((3840, 2160), (1920, 1080), "crop", False),
        ((3840, 2160), (1920, 1080), "blur", False),
        ((1920, 1080), (1920, 1080), "crop", False),
        # 1000x1000 into 2000x500: crop needs x2 to cover, bars fits at x0.5
        ((1000, 1000), (2000, 500), "crop", True),
        ((1000, 1000), (2000, 500), "bars", False),
        ((1000, 1000), (2000, 500), "blur", False),
    ],
)
def test_is_upscale(src, target, mode, expected):
    assert is_upscale(*src, *target, mode) is expected


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_w": 0, "target_h": 10},
        {"target_w": 10, "target_h": -5},
        {"target_w": 10, "target_h": 10, "mode": "stretch"},
        {"target_w": 10, "target_h": 10, "focus": (-0.1, 0.5)},
        {"target_w": 10, "target_h": 10, "focus": (0.5, 1.1)},
    ],
)
def test_invalid_args_raise(kwargs):
    with pytest.raises(ValueError):
        fit_image(gradient(20, 20), **kwargs)


def test_invalid_args_raise_is_upscale():
    with pytest.raises(ValueError):
        is_upscale(10, 10, 10, 10, "stretch")
    with pytest.raises(ValueError):
        is_upscale(0, 10, 10, 10, "crop")


def test_invalid_image_raises():
    with pytest.raises(ValueError):
        fit_image(np.zeros((10, 10), dtype=np.uint8), 5, 5)


@pytest.mark.parametrize("mode", MODES)
def test_input_not_mutated(mode):
    img = gradient(90, 160)
    before = img.copy()
    fit_image(img, 50, 120, mode=mode)
    np.testing.assert_array_equal(img, before)


@pytest.mark.parametrize("mode", ["bars", "blur"])
def test_same_aspect_matches_plain_lanczos_crop(mode):
    from PIL import Image

    img = gradient(90, 160)  # 16:9
    expected = np.array(Image.fromarray(img).resize((320, 180), Image.Resampling.LANCZOS))
    crop = fit_image(img, 320, 180, mode="crop")
    np.testing.assert_array_equal(crop, expected)
    np.testing.assert_array_equal(fit_image(img, 320, 180, mode=mode), crop)
