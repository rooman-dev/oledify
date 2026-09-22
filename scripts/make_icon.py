"""Generate the OLEDify app icon: a glowing cyan-to-violet ring on pure black.

Writes assets/icon.png (512 px) and assets/icon.ico (16-256 px). Run from the repo root:
    python scripts/make_icon.py
"""

from pathlib import Path

import numpy as np
from PIL import Image

SIZE = 512
SUPERSAMPLE = 2
ICO_SIZES = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
CYAN = np.array([0, 229, 255], dtype=np.float32)
VIOLET = np.array([150, 60, 255], dtype=np.float32)

RING_RADIUS = 0.34   # fraction of the icon size
RING_WIDTH = 0.045   # core thickness (Gaussian sigma), fraction of size
GLOW_WIDTH = 0.11    # soft halo sigma, fraction of size
GLOW_STRENGTH = 0.55

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def render(size: int) -> Image.Image:
    n = size * SUPERSAMPLE
    coords = (np.arange(n, dtype=np.float32) + 0.5) / n - 0.5
    x, y = np.meshgrid(coords, coords)
    radius = np.hypot(x, y)
    angle = np.arctan2(y, x)

    distance = radius - RING_RADIUS
    core = np.exp(-0.5 * (distance / (RING_WIDTH / 2)) ** 2)
    glow = GLOW_STRENGTH * np.exp(-0.5 * (distance / (GLOW_WIDTH / 2)) ** 2)
    intensity = np.clip(core + glow, 0.0, 1.0)

    # Cyan at the top-left sweeping to violet at the bottom-right; a sine keeps it seamless.
    t = (1.0 + np.sin(angle + np.pi / 4)) / 2.0
    colour = CYAN * (1.0 - t[..., None]) + VIOLET * t[..., None]
    # Whiten the very centre of the ring slightly so it reads as light, not paint.
    colour = colour + (255.0 - colour) * (0.35 * core**4)[..., None]

    rgb = colour * intensity[..., None]
    image = Image.fromarray(np.clip(np.rint(rgb), 0, 255).astype(np.uint8))
    image = image.resize((size, size), Image.Resampling.LANCZOS)

    # True black background: anything too faint to see becomes exactly (0, 0, 0).
    data = np.array(image)
    data[data.max(axis=2) < 3] = 0
    return Image.fromarray(data)


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    icon = render(SIZE)
    icon.save(ASSETS / "icon.png", optimize=True)
    icon.save(ASSETS / "icon.ico", sizes=ICO_SIZES)
    print(f"wrote {ASSETS / 'icon.png'} and {ASSETS / 'icon.ico'}")


if __name__ == "__main__":
    main()
