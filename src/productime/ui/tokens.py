"""Design token parsing and WCAG contrast math. Pure Python: no GTK objects are created here."""

import re
from pathlib import Path

_DEFINE_RE = re.compile(r"@define-color\s+([A-Za-z0-9_]+)\s+([^;]+);")
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def parse_tokens(css_text: str) -> dict[str, str]:
    """Read `@define-color name value;` lines, following `@other` references to a hex value."""
    raw = {name: value.strip() for name, value in _DEFINE_RE.findall(css_text)}
    resolved: dict[str, str] = {}
    for name in raw:
        value, hops = raw[name], 0
        while value.startswith("@") and value[1:] in raw and hops < 8:
            value, hops = raw[value[1:]], hops + 1
        if _HEX_RE.match(value):
            resolved[name] = value.upper()
    return resolved


def load_tokens(css_path: Path) -> dict[str, str]:
    return parse_tokens(Path(css_path).read_text(encoding="utf-8"))


def hex_to_rgb(color: str) -> tuple[float, float, float]:
    return tuple(int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))


def relative_luminance(color: str) -> float:
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(v) for v in hex_to_rgb(color))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    lighter, darker = sorted((relative_luminance(foreground), relative_luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)
