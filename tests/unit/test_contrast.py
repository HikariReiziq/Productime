import unittest

import _bootstrap  # noqa: F401

from productime.paths import styles_dir
from productime.ui.tokens import contrast_ratio, load_tokens, parse_tokens

SURFACES = ("bg_canvas", "bg_surface", "bg_surface_elevated")

# Normal-size text: WCAG AA needs 4.5:1.
TEXT_PAIRS = [
    *[("text_primary", s) for s in (*SURFACES, "bg_track")],
    *[("text_secondary", s) for s in (*SURFACES, "bg_track")],
    *[("text_muted", s) for s in (*SURFACES, "bg_track")],
    *[("accent_text", s) for s in SURFACES],
    *[("alert_text", s) for s in SURFACES],
    *[("success_text", s) for s in SURFACES],
    *[("rest_text", s) for s in SURFACES],
    ("on_accent", "accent_start"),
    ("on_accent", "accent_end"),
    ("on_success", "success"),
    ("on_duration_badge", "duration_badge"),
    ("success_text", "success_bg"),
    ("rest_text", "rest_bg"),
    ("alert_text", "alert_bg"),
    ("violet_text", "violet_bg"),
    ("cobalt_text", "cobalt_bg"),
    ("text_primary", "window_bg_color"),
    ("accent_fg_color", "accent_bg_color"),
]

# Rings, chart curve, focus outline, field borders, state dots: WCAG 1.4.11 needs 3:1.
GRAPHIC_PAIRS = [
    *[("ring_start", s) for s in (*SURFACES, "bg_track")],
    *[("ring_end", s) for s in (*SURFACES, "bg_track")],
    *[("focus_violet", s) for s in SURFACES],
    *[("border_strong", s) for s in SURFACES],
    *[("accent_text", s) for s in (*SURFACES, "bg_track")],
]


def blend(first: str, second: str) -> str:
    channels = [(int(first[i : i + 2], 16) + int(second[i : i + 2], 16)) // 2 for i in (1, 3, 5)]
    return "#" + "".join(f"{c:02X}" for c in channels)


class ContrastMathTests(unittest.TestCase):
    def test_black_on_white_is_21_to_1(self):
        self.assertAlmostEqual(contrast_ratio("#000000", "#FFFFFF"), 21.0, places=2)
        self.assertAlmostEqual(contrast_ratio("#FFFFFF", "#000000"), 21.0, places=2)

    def test_identical_colors_have_no_contrast(self):
        self.assertAlmostEqual(contrast_ratio("#215BF0", "#215BF0"), 1.0)

    def test_known_reference_pair(self):
        # #767676 on white is the classic 4.54:1 AA boundary grey.
        self.assertAlmostEqual(contrast_ratio("#767676", "#FFFFFF"), 4.54, places=2)

    def test_named_color_references_are_followed(self):
        tokens = parse_tokens("@define-color a #112233;\n@define-color b @a;\n@define-color c @b;")

        self.assertEqual(tokens, {"a": "#112233", "b": "#112233", "c": "#112233"})


class ThemeContrastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.themes = {name: load_tokens(styles_dir() / f"{name}.css") for name in ("light", "dark")}

    def test_both_themes_define_the_same_tokens(self):
        self.assertEqual(set(self.themes["light"]), set(self.themes["dark"]))
        self.assertGreaterEqual(len(self.themes["light"]), 30)

    def test_design_surface_and_ink_values_match_the_specification(self):
        expected = {
            "light": {
                "bg_canvas": "#FFFFFF",
                "bg_surface": "#FAFAFF",
                "text_primary": "#070417",
                "text_secondary": "#4A475C",
                "text_muted": "#5F5B73",
                "accent_text": "#1D4ED8",
            },
            "dark": {
                "bg_canvas": "#070417",
                "bg_surface": "#292639",
                "text_primary": "#FFFFFF",
                "text_secondary": "#C9C6D8",
                "text_muted": "#A7A3BA",
                "accent_text": "#A5B4FC",
            },
        }
        for theme, values in expected.items():
            for token, value in values.items():
                self.assertEqual(self.themes[theme][token], value, f"{theme}.{token}")

    def test_text_meets_wcag_aa_in_both_themes(self):
        for theme, tokens in self.themes.items():
            for foreground, background in TEXT_PAIRS:
                ratio = contrast_ratio(tokens[foreground], tokens[background])
                with self.subTest(theme=theme, pair=f"{foreground} on {background}"):
                    self.assertGreaterEqual(ratio, 4.5, f"{ratio:.2f}:1")

    def test_graphics_meet_three_to_one_in_both_themes(self):
        for theme, tokens in self.themes.items():
            for foreground, background in GRAPHIC_PAIRS:
                ratio = contrast_ratio(tokens[foreground], tokens[background])
                with self.subTest(theme=theme, pair=f"{foreground} on {background}"):
                    self.assertGreaterEqual(ratio, 3.0, f"{ratio:.2f}:1")

    def test_button_text_stays_readable_across_the_whole_gradient(self):
        for theme, tokens in self.themes.items():
            middle = blend(tokens["accent_start"], tokens["accent_end"])
            with self.subTest(theme=theme):
                self.assertGreaterEqual(contrast_ratio(tokens["on_accent"], middle), 4.5)

    def test_ink_on_the_success_fill_is_dark_because_white_fails(self):
        for theme, tokens in self.themes.items():
            with self.subTest(theme=theme):
                self.assertEqual(tokens["on_success"], "#070417")
                self.assertLess(contrast_ratio("#FFFFFF", tokens["success"]), 3.0)


if __name__ == "__main__":
    unittest.main()
