import numpy as np
import pytest

from oledify.core.oled import crush_blacks, luminance, true_black_percent


def solid(value, h=4, w=4):
    return np.full((h, w, 3), value, dtype=np.uint8)


def test_luminance_rec709():
    img = np.array([[[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 255]]], dtype=np.uint8)
    lum = luminance(img)
    assert lum.dtype == np.float32
    assert lum.shape == (1, 4)
    np.testing.assert_allclose(lum[0], [0.2126 * 255, 0.7152 * 255, 0.0722 * 255, 255], rtol=1e-5)


def test_pure_black_stays_black():
    assert not crush_blacks(solid(0)).any()


def test_pure_white_unchanged():
    img = solid(255)
    np.testing.assert_array_equal(crush_blacks(img), img)


def test_pixel_at_threshold_becomes_black():
    assert not crush_blacks(solid(16), threshold=16, falloff=24).any()


def test_pixel_above_falloff_unchanged():
    img = solid(40)  # luminance 40 == threshold + falloff
    np.testing.assert_array_equal(crush_blacks(img, threshold=16, falloff=24), img)
    img = solid(41)
    np.testing.assert_array_equal(crush_blacks(img, threshold=16, falloff=24), img)


def test_falloff_region_strictly_between():
    for value in (20, 28, 36):
        out = crush_blacks(solid(value), threshold=16, falloff=24)
        assert (out > 0).all(), value
        assert (out < value).all(), value


def test_falloff_zero_is_hard_cut():
    assert not crush_blacks(solid(16), threshold=16, falloff=0).any()
    img = solid(17)
    np.testing.assert_array_equal(crush_blacks(img, threshold=16, falloff=0), img)


def test_output_dtype_and_shape():
    img = np.random.default_rng(0).integers(0, 256, (7, 5, 3), dtype=np.uint8)
    out = crush_blacks(img)
    assert out.dtype == np.uint8
    assert out.shape == img.shape


def test_input_not_mutated():
    img = np.random.default_rng(1).integers(0, 256, (8, 8, 3), dtype=np.uint8)
    before = img.copy()
    crush_blacks(img)
    luminance(img)
    true_black_percent(img)
    np.testing.assert_array_equal(img, before)


def test_true_black_percent_2x2():
    img = np.array(
        [[[0, 0, 0], [0, 0, 1]],
         [[0, 0, 0], [255, 255, 255]]],
        dtype=np.uint8,
    )
    assert true_black_percent(img) == pytest.approx(50.0)


@pytest.mark.parametrize(
    "kwargs",
    [{"threshold": -1}, {"threshold": 256}, {"falloff": -1}],
)
def test_invalid_args_raise(kwargs):
    with pytest.raises(ValueError):
        crush_blacks(solid(10), **kwargs)


@pytest.mark.parametrize(
    "img",
    [
        np.zeros((4, 4), dtype=np.uint8),
        np.zeros((4, 4, 4), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.float32),
    ],
)
def test_invalid_image_raises(img):
    with pytest.raises(ValueError):
        crush_blacks(img)
