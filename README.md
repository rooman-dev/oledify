# OLEDify

Free, open-source Windows desktop app that converts any wallpaper for OLED/AMOLED screens.

OLEDify crushes near-black pixels to true black (`#000000`), so those pixels switch off completely on OLED panels. That gives deeper contrast and can save power. It also resizes the image to common screen resolutions. There's no AI and no network access: everything runs locally.

> Status: early development (v0.1.0 scaffold). Not usable yet.

## Planned features
- True-black crush with an adjustable threshold
- Resize/crop to presets: 4K, QHD, 2K, FHD, Ultrawide, Phone QHD+, Phone FHD+, iPhone Pro Max
- Live before/after preview
- Batch conversion
- Windows installer (Nuitka + Inno Setup)

## Run from source
Requires Python 3.12.

```powershell
git clone https://github.com/rooman-dev/oledify.git
cd oledify
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
oledify
```

Run the tests:

```powershell
pytest
```

## License
MIT © Rooman Ahmed. See [LICENSE](LICENSE).
