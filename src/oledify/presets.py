"""Output resolution presets: name -> (width, height) in pixels."""

PRESETS: dict[str, tuple[int, int]] = {
    "4K": (3840, 2160),
    "QHD": (2560, 1440),
    "2K": (2048, 1080),
    "FHD": (1920, 1080),
    "Ultrawide": (3440, 1440),
    "Phone QHD+": (1440, 3200),
    "Phone FHD+": (1080, 2400),
    "iPhone Pro Max": (1290, 2796),
}
