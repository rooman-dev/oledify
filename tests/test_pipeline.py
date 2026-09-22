import numpy as np
import pytest
from PIL import Image

from oledify.core.export import output_name
from oledify.core.pipeline import Settings, load_image, process, process_file

STATS_KEYS = {"true_black_percent", "upscaled", "src_size", "out_size"}


def check_rgb8(arr, h, w):
    assert arr.dtype == np.uint8
    assert arr.shape == (h, w, 3)


def test_load_rgba_composites_onto_black(tmp_path):
    data = np.zeros((4, 6, 4), dtype=np.uint8)
    data[..., :3] = 200
    data[:2, :, 3] = 255   # top half opaque
    data[2:, :, 3] = 0     # bottom half fully transparent
    Image.fromarray(data, "RGBA").save(tmp_path / "a.png")
    img = load_image(tmp_path / "a.png")
    check_rgb8(img, 4, 6)
    assert (img[:2] == 200).all()
    assert not img[2:].any()


def test_load_rgba_half_alpha(tmp_path):
    data = np.full((2, 2, 4), 200, dtype=np.uint8)
    data[..., 3] = 128
    Image.fromarray(data, "RGBA").save(tmp_path / "half.png")
    img = load_image(tmp_path / "half.png")
    assert np.abs(img.astype(int) - 100).max() <= 1


def test_load_greyscale(tmp_path):
    Image.fromarray(np.full((3, 5), 77, dtype=np.uint8), "L").save(tmp_path / "g.png")
    img = load_image(tmp_path / "g.png")
    check_rgb8(img, 3, 5)
    assert (img == 77).all()


def test_load_palette(tmp_path):
    src = np.zeros((4, 4, 3), dtype=np.uint8)
    src[:, :2] = (255, 0, 0)
    src[:, 2:] = (0, 0, 255)
    Image.fromarray(src).convert("P", palette=Image.Palette.ADAPTIVE, colors=4).save(tmp_path / "p.png")
    img = load_image(tmp_path / "p.png")
    check_rgb8(img, 4, 4)
    np.testing.assert_array_equal(img, src)


def test_load_palette_with_transparency(tmp_path):
    pal = Image.new("P", (2, 1))
    pal.putpalette([255, 255, 255, 0, 255, 0])
    pal.putpixel((0, 0), 0)  # white, marked transparent
    pal.putpixel((1, 0), 1)  # green
    pal.save(tmp_path / "pt.png", transparency=0)
    img = load_image(tmp_path / "pt.png")
    check_rgb8(img, 1, 2)
    assert not img[0, 0].any()
    assert tuple(img[0, 1]) == (0, 255, 0)


def test_load_16bit_greyscale(tmp_path):
    data = np.array([[0, 32896, 65535]], dtype=np.uint16)
    Image.fromarray(data).save(tmp_path / "16.png")
    with Image.open(tmp_path / "16.png") as raw:
        assert raw.mode.startswith("I")  # confirm the file really is 16-bit
    img = load_image(tmp_path / "16.png")
    check_rgb8(img, 1, 3)
    assert img[0, :, 0].tolist() == [0, 128, 255]


def test_load_cmyk(tmp_path):
    Image.new("CMYK", (3, 2), (0, 255, 255, 0)).save(tmp_path / "c.jpg", quality=100)
    img = load_image(tmp_path / "c.jpg")
    check_rgb8(img, 2, 3)
    r, g, b = img.reshape(-1, 3).mean(axis=0)
    assert r > 200 and g < 50 and b < 50


def test_load_exif_rotated_jpg(tmp_path):
    src = np.zeros((20, 40, 3), dtype=np.uint8)
    src[:, :20] = (255, 0, 0)   # left half red
    src[:, 20:] = (0, 0, 255)   # right half blue
    exif = Image.Exif()
    exif[0x0112] = 6            # display needs a 90 degree clockwise rotation
    Image.fromarray(src).save(tmp_path / "r.jpg", exif=exif, quality=95)
    img = load_image(tmp_path / "r.jpg")
    check_rgb8(img, 40, 20)      # width and height swapped
    top, bottom = img[:15].reshape(-1, 3).mean(0), img[25:].reshape(-1, 3).mean(0)
    assert top[0] > 200 and top[2] < 60      # red moved to the top
    assert bottom[2] > 200 and bottom[0] < 60


def gradient(h, w):
    x = np.linspace(0, 255, w, dtype=np.float32)
    img = np.repeat(np.broadcast_to(x, (h, w))[..., None], 3, axis=2)
    return img.astype(np.uint8)


def test_process_output_and_stats():
    img = gradient(90, 160)
    out, stats = process(img, 64, 36, Settings())  # same aspect keeps the dark left edge
    check_rgb8(out, 36, 64)
    assert set(stats) == STATS_KEYS
    assert stats["src_size"] == (160, 90)
    assert stats["out_size"] == (64, 36)
    assert 0 < stats["true_black_percent"] < 100


@pytest.mark.parametrize(
    "target, mode, expected",
    [((320, 180), "crop", True), ((80, 45), "crop", False), ((200, 50), "bars", False), ((200, 50), "crop", True)],
)
def test_process_upscaled_flag(target, mode, expected):
    _, stats = process(gradient(90, 160), *target, Settings(mode=mode, deband=False))
    assert stats["upscaled"] is expected


def test_process_applies_crush():
    img = np.full((10, 10, 3), 10, dtype=np.uint8)  # below default threshold
    out, stats = process(img, 10, 10, Settings())
    assert not out.any()
    assert stats["true_black_percent"] == 100.0


def test_process_without_deband_is_deterministic():
    img = gradient(90, 160)
    settings = Settings(deband=False)
    a, _ = process(img, 64, 48, settings)
    b, _ = process(img, 64, 48, settings)
    np.testing.assert_array_equal(a, b)


def test_process_does_not_mutate_input():
    img = gradient(90, 160)
    before = img.copy()
    process(img, 64, 48, Settings())
    np.testing.assert_array_equal(img, before)


@pytest.mark.parametrize("fmt", ["png", "jpg", "webp"])
def test_process_file_writes_one_file_per_target(tmp_path, fmt):
    src = tmp_path / "wall.png"
    Image.fromarray(gradient(90, 160)).save(src)
    targets = [(64, 36), (36, 64), (100, 100)]
    results = process_file(src, tmp_path / "out", targets, Settings(fmt=fmt))

    assert [path.name for path, _ in results] == [output_name(src, w, h, fmt) for w, h in targets]
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(p.name for p, _ in results)
    for (path, stats), (w, h) in zip(results, targets):
        assert stats["out_size"] == (w, h)
        with Image.open(path) as saved:
            assert saved.size == (w, h)


def test_process_same_seed_with_deband_is_identical():
    img = gradient(90, 160)
    settings = Settings(deband=True, seed=123)
    a, _ = process(img, 64, 36, settings)
    b, _ = process(img, 64, 36, settings)
    np.testing.assert_array_equal(a, b)
    c, _ = process(img, 64, 36, Settings(deband=True, seed=124))
    assert not np.array_equal(a, c)  # the seed really reaches deband


def test_process_file_order_matches_targets(tmp_path):
    src = tmp_path / "wall.png"
    Image.fromarray(gradient(90, 160)).save(src)
    # Largest first, so later (smaller, faster) targets would finish first if order leaked.
    targets = [(1200, 900), (8, 8), (640, 360), (16, 64), (300, 300), (32, 18)]
    results = process_file(src, tmp_path / "out", targets, Settings())
    assert [stats["out_size"] for _, stats in results] == targets
    assert [path.name for path, _ in results] == [output_name(src, w, h, "png") for w, h in targets]


def test_process_file_empty_targets(tmp_path):
    src = tmp_path / "wall.png"
    Image.fromarray(gradient(9, 16)).save(src)
    assert process_file(src, tmp_path / "out", [], Settings()) == []
