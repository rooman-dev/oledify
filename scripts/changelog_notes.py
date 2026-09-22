"""Print the CHANGELOG section for one version, for use as GitHub Release notes.

    python scripts/changelog_notes.py 0.1.0
"""

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def notes_for(version: str, text: str) -> str:
    """The body of the "## [version]" section, without its heading."""
    pattern = rf"^## \[{re.escape(version)}\].*?$(.*?)(?=^## \[|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    version = sys.argv[1]
    body = notes_for(version, CHANGELOG.read_text(encoding="utf-8"))
    if not body:
        body = f"See the [changelog](https://github.com/rooman-dev/oledify/blob/main/CHANGELOG.md) for {version}."
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
