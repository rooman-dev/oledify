"""OLED black-crush engine. Pure NumPy; images are uint8 RGB arrays of shape HxWx3."""

import numpy as np

REC709_WEIGHTS = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def _check_image(img: np.ndarray) -> None:
    if not isinstance(img, np.ndarray) or img.dtype != np.uint8 or img.ndim != 3 or img.shape[2] != 3:
        raise ValueError("expected a uint8 RGB array of shape HxWx3")


def luminance(img: np.ndarray) -> np.ndarray:
    """Rec.709 luminance as float32 HxW in the range 0-255."""
    _check_image(img)
    return img.astype(np.float32) @ REC709_WEIGHTS


def crush_blacks(img: np.ndarray, threshold: float = 16, falloff: float = 24) -> np.ndarray:
    """Force near-black pixels to true black, with a smooth ramp above the threshold.

    Luminance <= threshold becomes (0, 0, 0); luminance >= threshold + falloff is
    left unchanged; in between, RGB is scaled by a smoothstep weight. falloff=0
    gives a hard cut. Returns a new array; the input is never modified.
    """
    _check_image(img)
    if not 0 <= threshold <= 255:
        raise ValueError(f"threshold must be in 0-255, got {threshold}")
    if falloff < 0:
        raise ValueError(f"falloff must be >= 0, got {falloff}")

    lum = luminance(img)
    if falloff == 0:
        return np.where((lum <= threshold)[..., None], np.uint8(0), img)

    t = np.clip((lum - threshold) / np.float32(falloff), 0.0, 1.0)
    weight = t * t * (3.0 - 2.0 * t)
    out = img.copy()
    out[weight == 0.0] = 0
    ramp = (weight > 0.0) & (weight < 1.0)
    out[ramp] = np.rint(img[ramp] * weight[ramp, None]).astype(np.uint8)
    return out


def true_black_percent(img: np.ndarray) -> float:
    """Percentage (0-100) of pixels that are exactly (0, 0, 0)."""
    _check_image(img)
    if img.shape[0] * img.shape[1] == 0:
        return 0.0
    return float(np.count_nonzero(~img.any(axis=2))) * 100.0 / (img.shape[0] * img.shape[1])
