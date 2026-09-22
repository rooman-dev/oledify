"""Build OLEDify into a standalone Windows folder with Nuitka.

    python scripts/build.py

Output: build/dist/OLEDify/OLEDify.exe plus its runtime files. Standalone (a folder),
not onefile: it starts faster and the installer packages the folder anyway.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from oledify import __version__  # noqa: E402

ENTRY = ROOT / "src" / "oledify" / "main.py"
OUT_DIR = ROOT / "build" / "dist"
APP_DIR = OUT_DIR / "OLEDify"
NUITKA_DIST = OUT_DIR / "main.dist"
COMPANY = "rooman-dev"
PRODUCT = "OLEDify"


def windows_version(version: str) -> str:
    """Windows metadata wants four numbers: 0.1.0 -> 0.1.0.0."""
    parts = (version.split("+")[0].split("-")[0].split(".") + ["0", "0", "0", "0"])[:4]
    return ".".join(part if part.isdigit() else "0" for part in parts)


def build_command(assume_yes: bool) -> list[str]:
    file_version = windows_version(__version__)
    command = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--enable-plugin=pyside6",
        "--windows-console-mode=disable",
        f"--windows-icon-from-ico={ROOT / 'assets' / 'icon.ico'}",
        f"--include-data-dir={ROOT / 'assets'}=assets",
        "--include-package=oledify",
        f"--company-name={COMPANY}",
        f"--product-name={PRODUCT}",
        f"--file-version={file_version}",
        f"--product-version={file_version}",
        "--file-description=OLEDify - wallpaper converter for OLED screens",
        "--copyright=Copyright (c) 2026 Rooman Ahmed. MIT License.",
        f"--output-dir={OUT_DIR}",
        "--output-filename=OLEDify.exe",
        "--remove-output",
    ]
    if assume_yes:
        command.append("--assume-yes-for-downloads")
    command.append(str(ENTRY))
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-assume-yes", action="store_true", help="prompt before downloading Nuitka helpers")
    args = parser.parse_args()

    command = build_command(assume_yes=not args.no_assume_yes)
    print(f"Building OLEDify {__version__} ({windows_version(__version__)})")
    print(" ".join(command), flush=True)

    shutil.rmtree(NUITKA_DIST, ignore_errors=True)
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    if not NUITKA_DIST.is_dir():
        print(f"error: expected {NUITKA_DIST} to exist", file=sys.stderr)
        return 1
    shutil.rmtree(APP_DIR, ignore_errors=True)
    NUITKA_DIST.rename(APP_DIR)

    exe = APP_DIR / "OLEDify.exe"
    size_mb = sum(f.stat().st_size for f in APP_DIR.rglob("*") if f.is_file()) / 1024 / 1024
    print(f"\nBuilt {exe} ({exe.stat().st_size / 1024 / 1024:.1f} MB exe, {size_mb:.0f} MB folder)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
