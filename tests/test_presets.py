from oledify.presets import PRESETS


def test_presets_not_empty():
    assert PRESETS


def test_every_preset_is_positive_int_pair():
    for name, size in PRESETS.items():
        assert isinstance(size, tuple), name
        assert len(size) == 2, name
        width, height = size
        for value in (width, height):
            assert type(value) is int, name
            assert value > 0, name


def test_names_unique_case_insensitive():
    names = [name.strip().lower() for name in PRESETS]
    assert len(names) == len(set(names))
