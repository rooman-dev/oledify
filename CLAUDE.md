# OLEDify

Windows desktop app that converts wallpapers for OLED/AMOLED screens: true-black crush plus resize to 4K/QHD/2K/phone presets. Free, open-source (MIT). Repo: github.com/rooman-dev/oledify

## Stack
- Python 3.12, PySide6 (UI), Pillow + NumPy (image processing)
- pytest for tests
- Packaging: Nuitka (not PyInstaller) + Inno Setup installer, built by GitHub Actions on version tags (`v*`)

## Layout
```
src/oledify/
  __init__.py      # __version__
  main.py          # entry point: oledify = oledify.main:main
  presets.py       # output resolution presets
  core/            # pure image processing (no Qt)
  ui/              # PySide6 widgets and workers
tests/             # pytest suite
assets/            # icons, sample images
installer/         # Inno Setup scripts
.github/workflows/ # CI and release builds
```

## Rules
- `src/oledify/core/` must never import PySide6. Keep it pure Python/Pillow/NumPy so it stays testable without a display.
- All image processing runs off the UI thread (QThread or QRunnable) and reports back through Qt signals. Never block the event loop.
- Tests use pytest. Cover core logic with unit tests.
- No AI/ML dependencies and no network access, at runtime or in dependencies.
- Keep the dependency list minimal; justify any new one.

## Commands
```
pip install -e ".[dev]"
pytest
python -m oledify.main
```
