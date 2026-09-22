import numpy as np
import pytest
from PIL import Image

from oledify.core.export import deband, output_name, save_image


def ramp_image():
    """Rows of grey levels covering black, the dark band and bright values."""
    levels = np.array([0, 5, 20, 40, 63, 64, 100, 255], dtype=np.uint8)
    img = np.repeat(levels[:, None], 32, axis=1)
    return np.repeat(img[..., None], 3, axis=2)


def test_deband_keeps_true_black():
    img = ramp_image()
    out = deband(img, strength=3, seed=0)
    assert not out[0].any()


def test_deband_leaves_bright_pixels_unchanged():
    img = ramp_image()
    out = deband(img, strength=3, seed=0)
    np.testing.assert_array_equal(out[5:], img[5:])  # luminance >= 64


def test_deband_changes_dark_band_within_strength():
    img = ramp_image()
    out = deband(img, strength=2, seed=0)
    band = slice(1, 5)
    diff = out[band].astype(int) - img[band].astype(int)
    assert diff.any()
    assert np.abs(diff).max() <= 2


def test_deband_same_seed_same_output():
    img = ramp_image()
    np.testing.assert_array_equal(deband(img, 2, seed=42), deband(img, 2, seed=42))


def test_deband_output_dtype_shape_and_input_not_mutated():
    img = ramp_image()
    before = img.copy()
    out = deband(img, seed=1)
    assert out.dtype == np.uint8
    assert out.shape == img.shape
    np.testing.assert_array_equal(img, before)


def test_deband_invalid_strength():
    with pytest.raises(ValueError):
        deband(ramp_image(), strength=-1)


@pytest.mark.parametrize(
    "src, w, h, fmt, expected",
    [
        ("C:/walls/sunset.jpg", 3840, 2160, "png", "sunset_3840x2160_oled.png"),
        ("space.png", 1440, 3200, "jpg", "space_1440x3200_oled.jpg"),
        ("my.photo.v2.webp", 2560, 1440, "webp", "my.photo.v2_2560x1440_oled.webp"),
    ],
)
def test_output_name(src, w, h, fmt, expected):
    assert output_name(src, w, h, fmt) == expected


def test_output_name_invalid_fmt():
    with pytest.raises(ValueError):
        output_name("a.png", 10, 10, "bmp")


def sample_image():
    rng = np.random.default_rng(0)
    return rng.integers(0, 256, (48, 64, 3), dtype=np.uint8)


def test_save_png_round_trip_exact(tmp_path):
    img = sample_image()
    path = save_image(img, tmp_path / "out.png", fmt="png")
    assert path == tmp_path / "out.png"
    with Image.open(path) as saved:
        assert saved.format == "PNG"
        np.testing.assert_array_equal(np.array(saved.convert("RGB")), img)


@pytest.mark.parametrize("fmt, pil_format", [("jpg", "JPEG"), ("webp", "WEBP")])
def test_save_lossy_round_trip_shape(tmp_path, fmt, pil_format):
    img = sample_image()
    path = save_image(img, tmp_path / f"out.{fmt}", fmt=fmt, quality=90)
    with Image.open(path) as saved:
        assert saved.format == pil_format
        assert np.array(saved.convert("RGB")).shape == img.shape


def test_save_webp_quality_100_is_lossless(tmp_path):
    img = sample_image()
    path = save_image(img, tmp_path / "out.webp", fmt="webp", quality=100)
    with Image.open(path) as saved:
        np.testing.assert_array_equal(np.array(saved.convert("RGB")), img)


def test_save_creates_parent_folders(tmp_path):
    path = save_image(sample_image(), tmp_path / "a" / "b" / "out.png")
    assert path.exists()


def test_save_does_not_overwrite(tmp_path):
    img = sample_image()
    first = save_image(img, tmp_path / "out.png")
    second = save_image(img, tmp_path / "out.png")
    third = save_image(img, tmp_path / "out.png")
    assert first.name == "out.png"
    assert second.name == "out_1.png"
    assert third.name == "out_2.png"


@pytest.mark.parametrize(
    "kwargs",
    [{"fmt": "bmp"}, {"quality": 0}, {"quality": 101}, {"fmt": "jpg", "quality": 150}],
)
def test_save_invalid_args(tmp_path, kwargs):
    with pytest.raises(ValueError):
        save_image(sample_image(), tmp_path / "out.png", **kwargs)
    assert not any(tmp_path.iterdir())
