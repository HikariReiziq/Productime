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
    ("on_accent", "accent"),
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
    *[("ring", s) for s in (*SURFACES, "bg_track")],
    *[("live_dot", s) for s in SURFACES],
    *[("accent", s) for s in ("bg_canvas", "bg_surface")],
    *[("control_fill", s) for s in ("bg_canvas", "bg_surface")],
    ("on_accent", "control_fill"),
    ("nav_active_icon", "nav_active_bg"),
    *[("border_strong", s) for s in SURFACES],
    *[("accent_text", s) for s in (*SURFACES, "bg_track")],
]


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
                "bg_canvas": "#F4F4F7",
                "bg_surface": "#FFFFFF",
                "text_primary": "#141417",
                "text_secondary": "#4D4D57",
                "text_muted": "#62626C",
                "accent": "#1F5FEA",
                "accent_text": "#1A56DB",
            },
            "dark": {
                "bg_canvas": "#000000",
                "bg_surface": "#17171A",
                "text_primary": "#F4F4F7",
                "text_secondary": "#C2C2CC",
                "text_muted": "#9E9EA8",
                "accent": "#1F5FEA",
                "accent_text": "#8AB4FF",
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

    def test_the_accent_is_one_flat_color_with_no_gradient_tokens(self):
        for theme, tokens in self.themes.items():
            with self.subTest(theme=theme):
                self.assertFalse({"accent_start", "accent_end", "ring_start", "ring_end"} & set(tokens))
        css = (styles_dir() / "base.css").read_text(encoding="utf-8")
        self.assertNotIn("gradient", css)

    def test_ink_on_the_success_fill_is_dark_because_white_fails(self):
        for theme, tokens in self.themes.items():
            with self.subTest(theme=theme):
                self.assertEqual(tokens["on_success"], "#070417")
                self.assertLess(contrast_ratio("#FFFFFF", tokens["success"]), 3.0)


if __name__ == "__main__":
    unittest.main()
