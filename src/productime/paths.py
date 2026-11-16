"""XDG data locations and bundled resource lookup for both a checkout and an install."""

import os
from pathlib import Path

APP_ID = "org.productime.Productime"
APP_NAME = "Productime"

PACKAGE_DIR = Path(__file__).resolve().parent


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME", "")
    # The XDG spec says a relative XDG_DATA_HOME must be ignored.
    root = Path(base) if os.path.isabs(base) else Path.home() / ".local" / "share"
    return root / "productime"


def sounds_dir(data_root: Path | None = None) -> Path:
    return (data_root or data_dir()) / "sounds"


def assets_dir() -> Path:
    candidates = (
        PACKAGE_DIR.parent / "Assets",
        PACKAGE_DIR.parent.parent / "Assets",
        Path("/usr/share/productime/Assets"),
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def styles_dir() -> Path:
    return PACKAGE_DIR / "ui" / "styles"


def icons_dir() -> Path:
    return PACKAGE_DIR / "ui" / "icons"


def brand_icon_path(size: int = 64) -> Path:
    return icons_dir() / f"productime-brand-{128 if size > 64 else 64}.png"
