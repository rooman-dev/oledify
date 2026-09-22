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


# --- progress, cancel and batch --------------------------------------------

import threading  # noqa: E402

from oledify.core.pipeline import Cancelled, process_batch  # noqa: E402


def write_source(path, h=90, w=160):
    Image.fromarray(gradient(h, w)).save(path)
    return path


def write_corrupt(path):
    path.write_bytes(b"this is not an image")
    return path


def test_process_file_reports_progress(tmp_path):
    src = write_source(tmp_path / "wall.png")
    calls = []
    targets = [(64, 36), (36, 64), (100, 100), (20, 20)]
    results = process_file(src, tmp_path / "out", targets, Settings(), on_progress=lambda d, t: calls.append((d, t)))
    assert len(results) == 4
    assert calls == [(1, 4), (2, 4), (3, 4), (4, 4)]  # serialised and increasing, even from threads


def test_process_file_cancelled_before_start(tmp_path):
    src = write_source(tmp_path / "wall.png")
    cancel = threading.Event()
    cancel.set()
    calls = []
    results = process_file(src, tmp_path / "out", [(64, 36), (36, 64)], Settings(),
                           on_progress=lambda d, t: calls.append((d, t)), cancel=cancel)
    assert results == []
    assert calls == []
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


def test_process_batch_with_corrupt_file(tmp_path):
    a = write_source(tmp_path / "a.png")
    bad = write_corrupt(tmp_path / "bad.png")
    c = write_source(tmp_path / "c.png")
    targets = [(64, 36), (36, 64)]
    calls = []
    results = process_batch([a, bad, c], tmp_path / "out", targets, Settings(),
                            on_progress=lambda d, t: calls.append((d, t)))

    assert list(results) == [a, bad, c]
    assert isinstance(results[bad], Exception) and not isinstance(results[bad], Cancelled)
    for src in (a, c):
        assert [p.name for p, _ in results[src]] == [output_name(src, w, h, "png") for w, h in targets]
    assert len(list((tmp_path / "out").iterdir())) == 4

    assert all(t == 6 for _, t in calls)
    dones = [d for d, _ in calls]
    assert dones == sorted(dones)
    assert dones[-1] == 6  # the failed file still counts, so progress completes


def test_process_batch_cancel_skips_remaining_sources(tmp_path):
    sources = [write_source(tmp_path / f"s{i}.png") for i in range(4)]
    cancel = threading.Event()

    def on_progress(done, total):
        if done == 2:  # first source (2 targets) finished
            cancel.set()

    results = process_batch(sources, tmp_path / "out", [(64, 36), (36, 64)], Settings(),
                            on_progress=on_progress, cancel=cancel)
    assert len(results[sources[0]]) == 2
    for src in sources[1:]:
        assert isinstance(results[src], Cancelled)
    assert len(list((tmp_path / "out").iterdir())) == 2


def test_process_batch_overrides_per_source(tmp_path):
    a = write_source(tmp_path / "a.png")
    b = write_source(tmp_path / "b.png")
    base = Settings(deband=False)
    overrides = {b: Settings(deband=False, focus=(1.0, 0.5))}
    results = process_batch([a, b], tmp_path / "out", [(40, 90)], base, overrides=overrides)
    with Image.open(results[a][0][0]) as ia, Image.open(results[b][0][0]) as ib:
        red_a, red_b = np.array(ia)[..., 0].mean(), np.array(ib)[..., 0].mean()
    assert red_b > red_a + 50  # b was cropped from the right (brighter) end of the gradient


def test_process_batch_deduplicates_and_handles_no_targets(tmp_path):
    a = write_source(tmp_path / "a.png")
    calls = []
    results = process_batch([a, str(a)], tmp_path / "out", [], Settings(), on_progress=lambda d, t: calls.append(1))
    assert results == {a: []}
    assert calls == []


# --- load_thumbnail ---------------------------------------------------------

from oledify.core.pipeline import load_thumbnail  # noqa: E402


@pytest.mark.parametrize(
    "src_size, expected",
    [((400, 200), (160, 80)), ((200, 400), (80, 160)), ((90, 160), (90, 160))],  # (h, w) -> (h, w), never enlarged
)
def test_load_thumbnail_max_dimension(tmp_path, src_size, expected):
    path = tmp_path / "t.png"
    Image.fromarray(gradient(*src_size)).save(path)
    thumb = load_thumbnail(path, 160)
    assert thumb.dtype == np.uint8
    assert thumb.shape == (*expected, 3)
    assert max(thumb.shape[:2]) <= 160


def test_load_thumbnail_custom_size(tmp_path):
    path = tmp_path / "t.png"
    Image.fromarray(gradient(200, 400)).save(path)
    assert load_thumbnail(path, 64).shape == (32, 64, 3)


def test_load_thumbnail_exif_rotated_jpg(tmp_path):
    src = np.zeros((200, 400, 3), dtype=np.uint8)
    src[:, :200] = (255, 0, 0)
    src[:, 200:] = (0, 0, 255)
    exif = Image.Exif()
    exif[0x0112] = 6  # display needs a 90 degree clockwise rotation
    path = tmp_path / "r.jpg"
    Image.fromarray(src).save(path, exif=exif, quality=95)

    thumb = load_thumbnail(path, 160)
    assert thumb.shape[0] > thumb.shape[1]  # portrait after rotation
    top = thumb[: thumb.shape[0] // 3].reshape(-1, 3).mean(0)
    bottom = thumb[-thumb.shape[0] // 3 :].reshape(-1, 3).mean(0)
    assert top[0] > 200 and bottom[2] > 200  # red moved to the top, blue to the bottom


def test_load_thumbnail_rgba_on_black(tmp_path):
    data = np.zeros((100, 100, 4), dtype=np.uint8)
    data[..., :3] = 200
    data[:50, :, 3] = 255
    path = tmp_path / "a.png"
    Image.fromarray(data, "RGBA").save(path)

    thumb = load_thumbnail(path, 50)
    assert thumb.shape == (50, 50, 3)
    assert thumb[:20].min() > 150  # opaque half keeps its colour
    assert not thumb[30:].any()  # transparent half is pure black


def test_load_thumbnail_16bit_and_palette(tmp_path):
    png16 = tmp_path / "16.png"
    Image.fromarray(np.full((80, 160), 32896, dtype=np.uint16)).save(png16)
    thumb = load_thumbnail(png16, 40)
    assert thumb.shape == (20, 40, 3)
    assert abs(int(thumb.mean()) - 128) <= 1

    pal = tmp_path / "p.png"
    Image.fromarray(gradient(80, 160)).convert("P", palette=Image.Palette.ADAPTIVE).save(pal)
    assert load_thumbnail(pal, 40).shape == (20, 40, 3)


def test_load_thumbnail_rejects_corrupt_file(tmp_path):
    bad = write_corrupt(tmp_path / "bad.png")
    with pytest.raises(Exception):
        load_thumbnail(bad)
