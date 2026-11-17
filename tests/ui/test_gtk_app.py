"""Native GTK 4 application tests: real window, real widgets, real GStreamer pipelines."""

import csv
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import _bootstrap

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk

from gtk_harness import NATIVE_PROBLEM, GtkAppTestCase, pump, pump_until, target_size, text_of
from productime.domain.models import TargetPhase
from productime.domain.target_engine import InvalidTransition
from productime.paths import styles_dir
from productime.ui.main_window import NAV_ITEMS
from productime.ui.tokens import hex_to_rgb, load_tokens
from productime.ui.views.alarm.alarm_ringing_dialog import LIMIT_REACHED as ALARM_LIMIT_REACHED
from productime.ui.views.alarm.alarm_ringing_dialog import PASTE_BLOCKED, AlarmRingingDialog
from productime.ui.views.target import vertical_timeline
from productime.ui.views.target.next_batch_dialog import NextBatchDialog
from productime.ui.views.target.subtask_gate_dialog import LIMIT_REACHED, SubtaskGateDialog
from productime.ui.views.timer.timer_expired_dialog import TimerExpiredDialog
from support import EXAMPLE_1, alarm_draft, make_wav, short_target, timer_draft

VIEWS = ("today", "alarm", "stopwatch", "timer", "report")


def tooltip_reaches(widget, top) -> bool:
    """GTK 4 shows the nearest ancestor's tooltip, which is how a spin field's +/- get theirs."""
    while widget is not None and widget is not top:
        if widget.get_tooltip_text():
            return True
        widget = widget.get_parent()
    return False


def buttons_under(widget):
    """Every button below a widget, skipping the window's and the bottom sheet's own close controls."""
    found, stack = [], [widget]
    while stack:
        current = stack.pop()
        if current.get_css_name() == "windowcontrols":
            continue
        if isinstance(current, Gtk.Button) and current.get_mapped():
            found.append(current)
        child = current.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


class ShellTests(GtkAppTestCase):
    def test_first_launch_shows_today_with_an_honest_empty_state(self):
        today = self.window.view("today")

        self.assertEqual(self.window.get_title(), "Productime")
        self.assertEqual(self.window.current, "today")
        self.assertEqual(today.states.state, "empty")
        self.assertEqual(today.states.empty_panel.title_label.get_text(), "No targets planned for today")
        self.assertTrue(today.states.empty_panel.action_button.get_mapped())
        self.assertTrue(self.window.rail.get_visible())
        self.assertFalse(self.window.bottom_bar.get_visible())
        self.assertFalse(self.window.live_pill.get_visible())
        self.assertFalse(self.window.alert_bar.get_visible())
        self.assertEqual(self.repo.list_targets() + self.repo.list_alarms() + self.repo.list_timers(), [])

    def test_all_five_views_open_from_the_rail(self):
        expected_state = {"today": "empty", "alarm": "empty", "timer": "empty", "report": "content"}

        for name in ("alarm", "stopwatch", "timer", "report", "today"):
            view = self.go(name)
            self.assertEqual(self.window.current, name)
            self.assertIs(self.window.stack.get_visible_child(), view)
            self.assertTrue(view.get_mapped(), name)
            if name in expected_state:
                self.assertEqual(view.states.state, expected_state[name], name)

        report = self.window.view("report")
        self.assertEqual(report.detail.get_visible_child_name(), "empty")
        self.assertEqual((report.task_value.get_text(), report.duration_value.get_text()), ("0", "0m"))

    def test_loading_state_is_shown_until_the_data_arrives(self):
        today = self.window.view("today")

        today.reload()
        self.assertEqual(today.states.state, "loading")
        pump(0.2)

        self.assertEqual(today.states.state, "empty")

    def test_database_failure_shows_an_error_state_with_a_working_retry(self):
        today = self.window.view("today")
        working = self.repo.list_targets

        def broken():
            raise sqlite3.OperationalError("database is locked")

        self.repo.list_targets = broken
        today.reload()
        pump(0.2)
        self.assertEqual(today.states.state, "error")
        self.assertIn("database is locked", today.states.error_panel.description_label.get_text())

        self.repo.list_targets = working
        self.click(today.states.error_panel.action_button)
        pump(0.2)

        self.assertEqual(today.states.state, "empty")

    def test_unopenable_database_shows_a_startup_error_instead_of_crashing(self):
        self.close_app()
        blocker = self.tmp / "blocked"
        blocker.write_text("this file sits where the data folder should be", encoding="utf-8")

        self.launch(data_dir=blocker / "productime")

        self.assertIsNotNone(self.app.startup_error)
        panel = self.app.window.error_panel
        self.assertEqual(panel.title_label.get_text(), "Productime could not open its data")
        self.assertTrue(panel.action_button.get_mapped())

    def test_theme_icon_restyles_gtk_and_cairo_and_saves_the_choice(self):
        button = self.window.theme_button()
        tokens = {name: load_tokens(styles_dir() / f"{name}.css") for name in ("light", "dark")}

        for _ in range(2):
            wanted_dark = not self.app.theme.dark
            self.click(button)
            pump(0.2)
            name = "dark" if wanted_dark else "light"

            self.assertEqual(self.app.theme.dark, wanted_dark)
            self.assertEqual(Adw.StyleManager.get_default().get_dark(), wanted_dark)
            self.assertEqual(self.repo.get_pref("theme", ""), name)
            self.assertEqual(self.app.theme.rgb("bg_canvas"), hex_to_rgb(tokens[name]["bg_canvas"]))
            self.assertEqual(self.app.theme.rgb("text_primary"), hex_to_rgb(tokens[name]["text_primary"]))
            expected_icon = "productime-sun-symbolic" if wanted_dark else "productime-moon-symbolic"
            self.assertEqual(button.get_icon_name(), expected_icon)
            for view_name in VIEWS:
                self.assertTrue(self.go(view_name).get_mapped())

    def test_theme_choice_survives_a_restart(self):
        for wanted_dark in (True, False):
            self.app.theme.set_dark(wanted_dark)
            self.close_app()

            self.launch()

            self.assertEqual(self.app.theme.dark, wanted_dark)
            tip = "Switch to light mode" if wanted_dark else "Switch to dark mode"
            self.assertEqual(self.window.theme_button().get_tooltip_text(), tip)

    def test_volume_and_mute_in_settings_drive_the_audio_service_and_are_saved(self):
        settings = self.open_settings()

        settings.volume_scale.set_value(35)
        pump(0.05)
        self.assertEqual((self.app.audio.volume, self.repo.get_pref("volume", "")), (35, "35"))
        self.assertAlmostEqual(self.app.audio.effective_volume, 0.35)
        settings.mute_row.set_active(True)
        pump(0.05)

        self.assertEqual((self.app.audio.effective_volume, self.repo.get_pref("muted", "")), (0.0, "1"))

    def test_keyboard_shortcuts_and_actions_reach_every_view(self):
        for index, name in enumerate(VIEWS, start=1):
            self.assertEqual(self.app.get_accels_for_action(f"win.nav::{name}"), [f"<Control>{index}"])
        self.assertEqual(self.app.get_accels_for_action("app.quit"), ["<Control>q"])
        self.assertEqual(self.app.get_accels_for_action("win.create"), ["<Control>n"])

        self.window.activate_action("win.nav", GLib.Variant("s", "timer"))
        pump(0.2)
        self.assertEqual(self.window.current, "timer")
        self.window.activate_action("win.create", None)
        pump(0.3)

        editor = self.window.view("timer").editor
        self.assertTrue(editor.save_button.get_mapped())
        self.press_escape(editor)
        self.assertFalse(editor.save_button.get_mapped())

    def test_tab_moves_focus_through_the_rail_in_visual_order(self):
        tools = [self.window.theme_button(), self.window.pin_button(), self.window.settings_icon()]
        order = [self.window.nav_button(name) for name in VIEWS] + tools
        for widget in order:
            self.assertTrue(widget.get_focusable())

        order[0].grab_focus()
        pump(0.05)
        self.assertIs(self.window.get_focus(), order[0])
        seen = []
        for _ in range(len(order) - 1):
            self.window.child_focus(Gtk.DirectionType.TAB_FORWARD)
            pump(0.02)
            seen.append(self.window.get_focus())

        self.assertEqual(seen, order[1:])

    def test_rail_is_80_pixels_wide_with_icon_only_items_and_page_actions_at_the_right_edge(self):
        today = self.window.view("today")

        self.assertEqual(target_size(self.window.rail)[0], 80)
        for name, title, _icon in NAV_ITEMS:
            button = self.window.nav_button(name)
            self.assertIsInstance(button.get_child(), Gtk.Image, name)
            self.assertEqual(button.get_tooltip_text(), title)
        ok, bounds = today.create_button.compute_bounds(today)
        self.assertTrue(ok)
        self.assertAlmostEqual(bounds.get_x() + bounds.get_width(), today.get_width(), delta=1)

    def test_rail_menu_items_are_grouped_at_the_top(self):
        rail = self.window.rail
        ok, brand = self.window.rail_brand.compute_bounds(rail)
        boxes = []
        for name in VIEWS:
            ok_item, bounds = self.window.nav_button(name).compute_bounds(rail)
            self.assertTrue(ok_item)
            boxes.append(bounds)
        ok_tools, tools = self.window.rail_tools.compute_bounds(rail)
        self.assertTrue(ok and ok_tools)

        gaps = [
            later.get_y() - (earlier.get_y() + earlier.get_height())
            for earlier, later in zip(boxes, boxes[1:], strict=False)
        ]
        self.assertLessEqual(max(gaps), 12, gaps)
        self.assertLessEqual(boxes[0].get_y() - (brand.get_y() + brand.get_height()), 24)
        report_bottom = boxes[-1].get_y() + boxes[-1].get_height()
        self.assertGreater(tools.get_y() - report_bottom, 100, "tools should stay at the bottom of the rail")

    def test_tool_icons_follow_changes_made_in_settings(self):
        for wanted_dark in (True, False, True):
            self.app.theme.set_dark(wanted_dark)
            pump(0.1)
            expected = "productime-sun-symbolic" if wanted_dark else "productime-moon-symbolic"
            self.assertEqual(self.window.theme_button().get_icon_name(), expected)

        settings = self.open_settings()
        settings.keep_running_row.set_active(False)
        pump(0.05)
        self.assertFalse(self.window.pin_button().get_active())
        self.assertIn("off", self.window.pin_button().get_tooltip_text())

    def test_pin_icon_switches_keep_running_and_explains_itself(self):
        pin = self.window.pin_button()
        self.assertTrue(pin.get_active())
        self.assertIn("on", pin.get_tooltip_text())

        self.click(pin)

        self.assertEqual(self.repo.get_pref("close_to_background", ""), "0")
        self.assertIn("off", pin.get_tooltip_text())

    def test_report_metric_badges_are_32_pixel_squares(self):
        report = self.go("report")

        for badge in (report.task_badge, report.duration_badge):
            self.assertEqual(target_size(badge), (32, 32))

    def test_every_icon_the_ui_names_exists_in_the_icon_theme(self):
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        source = "".join(
            path.read_text(encoding="utf-8")
            for path in (_bootstrap.ROOT / "src" / "productime" / "ui").rglob("*.py")
        )
        names = set(re.findall(r'"([a-z0-9-]+-symbolic)"', source))

        self.assertGreater(len(names), 12)
        for name in sorted(names):
            self.assertTrue(theme.has_icon(name), name)

    def test_every_button_in_every_view_is_at_least_44_pixels(self):
        self.repo.create_target(EXAMPLE_1)
        self.repo.create_alarm(alarm_draft(repeat_days=(1, 2, 3, 4, 5, 6, 7)))
        self.repo.create_timer(timer_draft())
        self.scheduler.refresh_alarms()

        for name in VIEWS:
            self.go(name)
            self.window.view(name).reload()
            pump(0.3)
            buttons = buttons_under(self.window.root)
            self.assertGreater(len(buttons), 5, name)
            for button in buttons:
                label = text_of(button) or button.get_tooltip_text() or "unnamed"
                width, height = target_size(button)
                self.assertGreaterEqual(height, 44, f"{name}: {label}")
                self.assertGreaterEqual(width, 44, f"{name}: {label}")


class CompactLayoutTests(GtkAppTestCase):
    window_size = (420, 720)

    def minimum_width(self) -> int:
        return self.window.get_content().measure(Gtk.Orientation.HORIZONTAL, -1)[0]

    def test_narrow_window_uses_the_bottom_bar(self):
        self.assertEqual(self.window.get_width(), 420)
        self.assertTrue(self.window.compact)
        self.assertFalse(self.window.rail.get_visible())
        self.assertTrue(self.window.bottom_bar.get_mapped())
        self.assertTrue(self.window.compact_brand.get_mapped())
        for name in VIEWS:
            self.assertGreaterEqual(target_size(self.window.nav_button(name, bottom=True))[1], 44)

        self.assertTrue(self.window.header_tools.get_mapped())
        self.click(self.window.settings_icon(compact=True))
        pump(0.4)

        dialog = self.window.settings_dialog
        self.assertTrue(dialog.save_row.get_mapped())
        self.press_escape(dialog)
        pump(0.2)
        self.assertIsNone(self.window.settings_dialog)

    def test_no_view_overflows_at_420_pixels_even_with_long_content(self):
        long_name = "Quarterly platform migration with a very long descriptive target name" + " x" * 5
        target = self.repo.create_target(replace(EXAMPLE_1, name=long_name[:80]))
        self.repo.create_alarm(
            alarm_draft(
                name=long_name[:80],
                repeat_days=(1, 2, 3, 4, 5, 6, 7),
                mission_mode="math",
                mission_count=3,
                mission_difficulty="hard",
            )
        )
        self.repo.create_timer(timer_draft(name=long_name[:80]))
        self.scheduler.refresh_alarms()
        self.scheduler.stopwatch_start()
        self.advance(3725.5)
        self.scheduler.stopwatch_lap()

        for name in VIEWS:
            self.go(name)
            self.window.view(name).reload()
            pump(0.3)
            self.assertLessEqual(self.minimum_width(), 420, name)
            self.assertEqual(self.window.get_width(), 420, name)

        today = self.go("today")
        self.click(today.rows[target.id]["play"])
        pump(0.2)
        self.assertEqual(today.pages.get_visible_child_name(), "focus")
        self.assertLessEqual(self.minimum_width(), 420)
        self.assertEqual(today.focus_view.panes.get_orientation(), Gtk.Orientation.VERTICAL)

    def test_running_session_pill_replaces_the_brand_name_when_narrow(self):
        target = self.repo.create_target(EXAMPLE_1)
        self.assertTrue(self.window.compact_brand_label.get_mapped())

        self.scheduler.start_target(target.id)
        pump(0.2)

        self.assertTrue(self.window.live_pill.get_mapped())
        self.assertFalse(self.window.compact_brand_label.get_visible())
        self.assertLessEqual(self.minimum_width(), 420)

    def test_editors_and_their_sheets_fit_a_narrow_window(self):
        editors = {}
        for name in ("today", "alarm", "timer"):
            _view, editors[name] = self.open_editor(name)
            pump(0.2)
            self.assertLessEqual(self.minimum_width(), 420, name)
            self.assertEqual(self.window.get_width(), 420, name)
            self.assertTrue(self.window.bottom_bar.get_mapped(), name)

        self.assertEqual(editors["today"].columns.get_orientation(), Gtk.Orientation.VERTICAL)
        alarm = editors["alarm"]
        self.go("alarm")
        for opener in (alarm.snooze_row, alarm.mission_slot):
            sheet = self.open_sheet(opener)
            self.assertLessEqual(sheet.get_width(), 420)
            for button in buttons_under(sheet):
                self.assertGreaterEqual(min(target_size(button)), 44, text_of(button))
            self.press_escape(sheet)


class EditorPageTests(GtkAppTestCase):
    """Create and Edit open a page in the content area, like a phone clock app, not a modal."""

    TITLES = {"today": "Create Target", "alarm": "New Alarm", "timer": "Add Timer"}

    def test_create_opens_a_page_beside_the_rail_not_a_dialog(self):
        for name, title in self.TITLES.items():
            view, editor = self.open_editor(name)
            self.assertIsNone(self.window.get_visible_dialog(), name)
            self.assertIs(self.window.content.get_visible_child(), editor, name)
            self.assertFalse(view.get_mapped(), name)
            self.assertTrue(self.window.rail.get_mapped(), name)
            self.assertEqual(editor.title_label.get_text(), title)
            self.assertEqual(editor.close_button.get_tooltip_text(), "Close without saving")
            # The Save button stays in reach however long the form gets.
            self.assertFalse(editor.save_button.is_ancestor(editor.scroller), name)
            self.assertIs(self.window.get_focus(), editor.name_entry.get_delegate(), name)

            self.click(editor.close_button)
            pump(0.2)
            self.assertIsNone(view.editor, name)
            self.assertIs(self.window.content.get_visible_child(), self.window.views_page, name)
            self.assertTrue(view.get_mapped(), name)
        self.assertEqual(self.repo.list_targets() + self.repo.list_alarms() + self.repo.list_timers(), [])

    def test_escape_discards_the_page_and_returns_focus_to_create(self):
        alarms, editor = self.open_editor("alarm")
        editor.name_entry.set_text("Never saved")

        self.press_escape(editor)

        self.assertIsNone(alarms.editor)
        self.assertEqual(self.repo.list_alarms(), [])
        self.assertIs(self.window.get_focus(), alarms.create_button)

    def test_a_draft_survives_switching_views(self):
        alarms, editor = self.open_editor("alarm")
        editor.name_entry.set_text("Draft alarm")

        timers = self.go("timer")
        self.assertTrue(timers.get_mapped())
        self.assertFalse(editor.get_mapped())
        self.go("alarm")

        self.assertIs(alarms.editor, editor)
        self.assertTrue(editor.get_mapped())
        self.assertEqual(editor.name_entry.get_text(), "Draft alarm")

    def test_saving_closes_the_page_and_shows_the_new_item(self):
        timers, editor = self.open_editor("timer")
        editor.name_entry.set_text("Tea")

        self.click(editor.save_button)
        pump(0.3)

        self.assertIsNone(timers.editor)
        self.assertTrue(timers.get_mapped())
        self.assertEqual([t.name for t in self.repo.list_timers()], ["Tea"])

    def test_icon_only_controls_in_pages_and_sheets_name_themselves(self):
        _today, target = self.open_editor("today")
        _alarms, alarm = self.open_editor("alarm")
        _timers, timer = self.open_editor("timer")
        for name, page in (("today", target), ("alarm", alarm), ("timer", timer)):
            self.go(name)
            for button in buttons_under(page):
                if not text_of(button):
                    self.assertTrue(tooltip_reaches(button, page), f"{page.title_label.get_text()}: unnamed")
        self.go("alarm")
        for opener in (alarm.snooze_row, alarm.mission_slot):
            sheet = self.open_sheet(opener)
            for button in buttons_under(sheet):
                if not text_of(button):
                    self.assertTrue(tooltip_reaches(button, sheet), "sheet: unnamed")
            self.press_escape(sheet)
        for row in (alarm.snooze_row, target.long_rest_row, target.snooze_row):
            self.assertTrue(row.get_tooltip_text())
            self.assertIsInstance(row.get_child().get_last_child(), Gtk.Image)


class TargetFlowTests(GtkAppTestCase):
    def open_create_dialog(self):
        return self.open_editor("today")

    def set_long_rest(self, editor, every, minutes, custom_every=None):
        sheet = self.open_sheet(editor.long_rest_row)
        sheet.long_switch.set_active(True)
        pump(0.1)
        self.choose(sheet.groups["every"], every)
        if custom_every is not None:
            sheet.every_spin.set_value(custom_every)
        self.choose(sheet.groups["minutes"], minutes)
        self.click(sheet.save_button)
        pump(0.2)

    def set_snooze_limit(self, editor, limit):
        sheet = self.open_sheet(editor.snooze_row)
        group = sheet.groups["limit"]
        if not group.button_for(limit).get_mapped():
            self.click(group.more_button)
        self.choose(group, limit)
        self.click(sheet.save_button)
        pump(0.2)

    def start_short_target(self, **overrides):
        target = self.repo.create_target(short_target(**overrides))
        today = self.window.view("today")
        today.reload()
        pump(0.2)
        self.click(today.rows[target.id]["play"])
        pump(0.2)
        return today, target

    @property
    def phase(self):
        return self.scheduler.session.state.phase

    def test_create_target_projects_example_1_as_6h_40m_and_saves_every_subtask(self):
        today, dialog = self.open_create_dialog()
        self.assertEqual(len(dialog.sound_picker.sound_names()), 6)

        dialog.name_entry.set_text("Website Profile Company")
        self.choose(dialog.tag_group, "Coding")
        dialog.hours_spin.set_value(6)
        dialog.minutes_spin.set_value(0)
        self.choose(dialog.pomodoro_group, "50/10")
        self.assertEqual(text_of(dialog.long_rest_row), "Long rest Off")
        self.set_long_rest(dialog, 3, 30)
        self.assertEqual(text_of(dialog.long_rest_row), "Long rest Every 3, 30 min")
        self.set_snooze_limit(dialog, 3)
        self.assertEqual(text_of(dialog.snooze_row), "Snooze limit 3 times")

        self.assertEqual(dialog.timeline.total_text, "Total Timeline: 6h 40m")
        entries = dialog.timeline.entries()
        self.assertEqual(len(entries), 12)
        self.assertEqual(
            [entry[2] for entry in entries[:4]],
            ["00:00 -> 00:50", "00:50 -> 01:00", "01:00 -> 01:50", "01:50 -> 02:00"],
        )
        self.assertEqual(entries[5][:3], ("long", "Long rest", "02:50 -> 03:20"))
        self.assertEqual(entries[-1][:3], ("long", "Long rest", "06:10 -> 06:40"))
        self.assertEqual(len(dialog.subtask_entries), 6)

        for entry, title in zip(dialog.subtask_entries, EXAMPLE_1.subtask_titles, strict=True):
            entry.set_text(title)
        pump(0.1)
        self.assertEqual(dialog.timeline.entries()[0][1], "Batch 1: Membuat PRD")
        self.assertEqual(dialog.timeline.entries()[10][1], "Batch 6: Finish selesai semua")
        self.click(dialog.save_button)
        pump(0.3)

        target = self.repo.list_targets()[0]
        self.assertEqual(target.total_timeline_minutes, 400)
        self.assertEqual([b.subtask_title for b in target.batches], list(EXAMPLE_1.subtask_titles))
        self.assertEqual((target.tag_label, target.snooze_limit), ("Coding", 3))
        self.assertEqual(today.states.state, "content")
        self.assertEqual(today.rows[target.id]["total"].get_text(), "6h 40m total")
        self.assertEqual(today.hero_time.get_text(), "06:40:00")

    def test_create_target_projects_example_2_as_4h_50m(self):
        _today, dialog = self.open_create_dialog()

        dialog.name_entry.set_text("Belajar Pemrograman Mobile")
        dialog.hours_spin.set_value(4)
        self.choose(dialog.pomodoro_group, "25/5")
        self.set_long_rest(dialog, "custom", 30, custom_every=4)
        self.set_snooze_limit(dialog, None)
        self.assertEqual(text_of(dialog.snooze_row), "Snooze limit Unlimited")
        pump(0.1)

        self.assertEqual(dialog.timeline.total_text, "Total Timeline: 4h 50m")
        entries = dialog.timeline.entries()
        self.assertEqual(len(entries), 16)
        self.assertEqual(entries[7][:3], ("long", "Long rest", "01:55 -> 02:25"))
        self.assertEqual(entries[-1][:3], ("long", "Long rest", "04:20 -> 04:50"))
        self.assertEqual(len(dialog.subtask_entries), 8)
        self.click(dialog.save_button)
        pump(0.3)

        target = self.repo.list_targets()[0]
        self.assertEqual((target.total_timeline_minutes, target.snooze_limit), (290, None))
        self.assertEqual(target.batches[7].subtask_title, "Batch 8")

    def test_custom_pomodoro_and_remainder_batches_update_the_live_timeline(self):
        _today, dialog = self.open_create_dialog()

        dialog.hours_spin.set_value(1)
        dialog.minutes_spin.set_value(40)
        self.choose(dialog.pomodoro_group, "25/5")
        pump(0.1)
        self.assertEqual(dialog.timeline.total_text, "Total Timeline: 1h 45m")
        self.assertEqual(len(dialog.subtask_entries), 4)

        sheet = self.open_sheet(dialog.pomodoro_group.button_for("custom"))
        sheet.work_spin.set_value(45)
        sheet.break_spin.set_value(15)
        self.click(sheet.save_button)
        pump(0.2)

        self.assertEqual(dialog.pomodoro_group.get_value(), "custom")
        self.assertEqual(text_of(dialog.pomodoro_group.button_for("custom")), "45 / 15")
        self.assertEqual(dialog.timeline.total_text, "Total Timeline: 1h 55m")
        self.assertEqual(
            [entry[2] for entry in dialog.timeline.entries()],
            ["00:00 -> 00:45", "00:45 -> 01:00", "01:00 -> 01:40", "01:40 -> 01:55"],
        )

    def test_cancelling_the_custom_pomodoro_sheet_keeps_the_preset(self):
        _today, dialog = self.open_create_dialog()
        self.choose(dialog.pomodoro_group, "50/10")

        sheet = self.open_sheet(dialog.pomodoro_group.button_for("custom"))
        sheet.work_spin.set_value(45)
        self.click(sheet.cancel_button)
        pump(0.2)

        self.assertEqual(dialog.pomodoro_group.get_value(), "50/10")
        self.assertEqual(text_of(dialog.pomodoro_group.button_for("custom")), "Custom")
        self.assertEqual(dialog.current_config().work_minutes, 50)

    def test_long_rest_sheet_starts_off_and_cancel_changes_nothing(self):
        _today, dialog = self.open_create_dialog()
        before = dialog.timeline.total_text

        sheet = self.open_sheet(dialog.long_rest_row)
        self.assertFalse(sheet.long_switch.get_active())
        self.assertFalse(sheet.groups["every"].get_mapped())
        sheet.long_switch.set_active(True)
        pump(0.1)
        self.assertTrue(sheet.groups["every"].get_mapped())
        self.press_escape(sheet)

        self.assertEqual(text_of(dialog.long_rest_row), "Long rest Off")
        self.assertEqual(dialog.timeline.total_text, before)

    def test_category_chips_carry_icons_and_names(self):
        _today, dialog = self.open_create_dialog()

        for value in ("Work", "Study", "Coding", "Personal", "custom"):
            button = dialog.tag_group.button_for(value)
            self.assertIsInstance(button.get_child().get_first_child(), Gtk.Image, value)
            self.assertTrue(button.get_tooltip_text(), value)

    def test_custom_category_opens_a_sheet_and_is_saved(self):
        today, dialog = self.open_create_dialog()
        self.assertEqual(dialog.tag_group.get_value(), "Study")

        sheet = self.open_sheet(dialog.tag_group.button_for("custom"))
        self.assertIs(self.window.get_focus(), sheet.tag_entry.get_delegate())
        self.click(sheet.save_button)
        self.assertEqual(sheet.error_label.get_text(), "Category: This field cannot be empty.")
        self.assertTrue(sheet.save_button.get_mapped())
        self.type_text(sheet.tag_entry, "Skripsi")
        self.click(sheet.save_button)
        pump(0.2)

        self.assertEqual(dialog.tag_group.get_value(), "custom")
        self.assertEqual(text_of(dialog.tag_group.button_for("custom")), "Skripsi")
        dialog.name_entry.set_text("Thesis")
        self.click(dialog.save_button)
        pump(0.3)

        target = self.repo.list_targets()[0]
        self.assertEqual((target.tag_label, target.tag_color), ("Skripsi", "gray"))
        row = today.rows[target.id]["row"]
        texts = [label.get_text() for label in self.labels_under(row)]
        self.assertIn("Skripsi", texts)
        self.assertIn("S", texts)
        self.assertNotIn(
            "skripsi", [name for label in self.labels_under(row) for name in label.get_css_classes()]
        )

    def test_editing_a_custom_category_target_keeps_the_name(self):
        from dataclasses import replace

        target = self.repo.create_target(replace(EXAMPLE_1, tag_label="Skripsi"))
        today = self.window.view("today")
        today.reload()
        pump(0.2)

        self.click(today.rows[target.id]["edit"])
        pump(0.3)

        dialog = today.editor
        self.assertEqual(dialog.title_label.get_text(), "Edit Target")
        self.assertEqual(dialog.tag_group.get_value(), "custom")
        self.assertEqual(text_of(dialog.tag_group.button_for("custom")), "Skripsi")
        self.assertEqual(text_of(dialog.long_rest_row), "Long rest Every 3, 30 min")
        sheet = self.open_sheet(dialog.tag_group.button_for("custom"))
        self.assertEqual(sheet.tag_entry.get_text(), "Skripsi")

    def test_cancelling_the_category_sheet_returns_to_the_previous_category(self):
        _today, dialog = self.open_create_dialog()
        self.choose(dialog.tag_group, "Work")

        sheet = self.open_sheet(dialog.tag_group.button_for("custom"))
        self.type_text(sheet.tag_entry, "Never kept")
        self.click(sheet.cancel_button)
        pump(0.2)

        self.assertEqual(dialog.tag_group.get_value(), "Work")
        self.assertEqual(text_of(dialog.tag_group.button_for("custom")), "Custom")
        self.assertEqual(dialog.tag_label(), "Work")

    def test_invalid_target_shows_an_inline_error_and_saves_nothing(self):
        today, dialog = self.open_create_dialog()

        self.click(dialog.save_button)
        self.assertTrue(dialog.error_label.get_mapped())
        self.assertEqual(dialog.error_label.get_text(), "Target name: This field cannot be empty.")
        dialog.name_entry.set_text("Plan")
        dialog.hours_spin.set_value(0)
        dialog.minutes_spin.set_value(0)
        pump(0.1)
        self.assertTrue(dialog.timeline.message_label.get_mapped())
        self.click(dialog.save_button)

        self.assertEqual(dialog.error_label.get_text(), "Set a target duration of at least 1 minute.")
        self.assertTrue(dialog.save_button.get_mapped())
        self.assertEqual(self.repo.list_targets(), [])

    def test_escape_closes_the_create_page_without_saving(self):
        today, dialog = self.open_create_dialog()
        dialog.name_entry.set_text("Never saved")

        self.press_escape(dialog)

        self.assertFalse(dialog.save_button.get_mapped())
        self.assertIsNone(today.editor)
        self.assertEqual(self.repo.list_targets(), [])
        self.assertEqual(today.states.state, "empty")
        self.assertTrue(today.states.empty_panel.get_mapped())

    def test_gate_blocks_the_break_until_the_subtask_is_checked_off(self):
        today, _target = self.start_short_target(snooze_limit=1)
        focus = today.focus_view
        self.assertEqual(today.pages.get_visible_child_name(), "focus")
        self.assertEqual((focus.ring.text, focus.ring.tone), ("01:00", "focus"))
        self.assertTrue(self.window.live_pill.get_mapped())

        self.advance(30)
        self.assertEqual(focus.ring.text, "00:30")
        self.assertAlmostEqual(focus.ring.fraction, 0.5)
        self.advance(30)

        gate = self.window.alert_dialog
        self.assertIsInstance(gate, SubtaskGateDialog)
        self.assertTrue(gate.complete_button.get_mapped())
        self.assertEqual(self.phase, TargetPhase.AWAITING_SUBTASK_GATE)
        self.assertTrue(self.audio_playing())
        self.assertEqual(self.app.audio.alert_sound_id, "bundled-community")
        self.assertEqual(gate.heading_label.get_text(), "Batch 1 complete")
        self.assertEqual(gate.prompt_label.get_text(), "First step")
        self.assertEqual(text_of(gate.complete_button), "Check Subtask & Start Break")
        self.assertEqual(gate.snooze_status.get_text(), "1 of 1 snoozes left")
        self.assertEqual(sorted(gate.snooze_buttons), [1, 3, 5, 10])

        self.advance(45 * 60)
        self.assertEqual(self.phase, TargetPhase.AWAITING_SUBTASK_GATE)
        self.assertEqual(self.app.audio.alert_state(), "playing")

        self.click(gate.snooze_buttons[1])
        self.assertEqual(self.phase, TargetPhase.SNOOZING)
        self.assertIsNone(self.window.alert_dialog)
        self.assertEqual(self.app.audio.alert_state(), "null")
        self.advance(60)

        gate = self.window.alert_dialog
        self.assertIsInstance(gate, SubtaskGateDialog)
        self.assertEqual(self.phase, TargetPhase.AWAITING_SUBTASK_GATE)
        for button in (*gate.snooze_buttons.values(), gate.custom_button):
            self.assertFalse(button.is_sensitive())
        self.assertEqual(gate.snooze_status.get_text(), LIMIT_REACHED)
        with self.assertRaises(InvalidTransition):
            self.scheduler.snooze_session(1)

        self.click(gate.complete_button)

        self.assertEqual(self.phase, TargetPhase.RESTING)
        self.assertIsNone(self.window.alert_dialog)
        self.assertEqual(self.app.audio.alert_state(), "null")
        self.assertEqual(focus.ring.tone, "rest")
        self.assertTrue(focus.complete_button.is_sensitive())
        self.assertEqual(focus.complete_button.get_tooltip_text(), "Start the next batch")

    def test_gate_dialog_is_centred_with_one_even_row_of_snooze_choices(self):
        self.start_short_target(snooze_limit=3)
        self.advance(60)
        gate = self.window.alert_dialog
        body = gate.body
        width = body.get_width()

        self.assertEqual(gate.heading_label.get_xalign(), 0.5)
        ok, primary = gate.complete_button.compute_bounds(body)
        self.assertTrue(ok)
        self.assertAlmostEqual(primary.get_width(), width, delta=1)
        tops, widths = set(), set()
        for button in gate.snooze_buttons.values():
            ok, bounds = button.compute_bounds(body)
            self.assertTrue(ok)
            tops.add(round(bounds.get_y()))
            widths.add(round(bounds.get_width()))
            self.assertGreaterEqual(min(bounds.get_width(), bounds.get_height()), 44)
        self.assertEqual(len(tops), 1, "snooze choices share one row")
        self.assertLessEqual(max(widths) - min(widths), 1, "snooze choices share the width evenly")
        ok, quit_bounds = gate.quit_button.compute_bounds(body)
        self.assertTrue(ok)
        self.assertGreater(quit_bounds.get_y(), max(tops))

    def test_custom_snooze_minutes_extend_the_batch(self):
        self.start_short_target(snooze_limit=None)
        self.advance(60)
        gate = self.window.alert_dialog
        self.assertEqual(gate.snooze_status.get_text(), "Unlimited snoozes for this target.")

        gate.custom_spin.set_value(7)
        self.click(gate.custom_button)

        self.assertEqual(self.phase, TargetPhase.SNOOZING)
        self.assertEqual(self.scheduler.session.state.remaining_ms, 7 * 60_000)

    def test_final_batch_enters_final_rest_and_offers_finish_target(self):
        today, target = self.start_short_target()
        focus = today.focus_view

        self.click(focus.complete_button)
        self.assertEqual(self.phase, TargetPhase.RESTING)
        self.assertFalse(focus.finish_button.get_visible())
        self.advance(60)
        self.assertEqual(self.phase, TargetPhase.READY_FOR_NEXT)
        self.click(self.window.alert_dialog.start_button)
        self.assertEqual((self.phase, self.scheduler.session.state.batch_index), (TargetPhase.WORKING, 2))
        self.assertFalse(focus.complete_button.is_sensitive() and self.phase is TargetPhase.RESTING)
        self.advance(60)
        gate = self.window.alert_dialog
        self.assertEqual(text_of(gate.complete_button), "Check Subtask & Enter Final Rest")
        self.click(gate.complete_button)

        self.assertTrue(self.scheduler.session.state.in_final_rest)
        self.assertFalse(focus.complete_button.is_sensitive(), "the final rest has no next batch")
        self.assertTrue(focus.finish_button.get_mapped())
        self.assertEqual(text_of(focus.finish_button), "Finish Target / Skip Final Rest")
        states = [entry[3] for entry in focus.timeline.entries()]
        self.assertEqual(states, ["done", "done", "done", "active"])
        self.click(focus.finish_button)

        self.assertIsNone(self.scheduler.session)
        self.assertEqual(self.repo.get_target(target.id).status, "completed")
        self.assertEqual(today.pages.get_visible_child_name(), "list")
        self.assertEqual(self.window.last_toast, "Target complete: Quick target")
        self.assertIn("Target complete", self.notifier.titles())
        report = self.go("report")
        self.assertEqual((report.task_value.get_text(), report.duration_value.get_text()), ("2", "1m"))
        self.assertEqual({row[1] for row in report.log_table()}, {"First step", "Second step"})
        self.assertEqual({row[4] for row in report.log_table()}, {"Completed"})

    def test_escape_on_the_gate_leaves_an_alert_bar_and_keeps_the_sound_looping(self):
        today, _target = self.start_short_target()
        self.advance(60)
        gate = self.window.alert_dialog
        self.assertTrue(self.audio_playing())

        self.press_escape(gate)

        self.assertIsNone(self.window.alert_dialog)
        self.assertTrue(self.window.alert_bar.get_mapped())
        self.assertEqual(self.window.alert_bar_label.get_text(), "Waiting for check-off: First step")
        self.assertEqual(self.phase, TargetPhase.AWAITING_SUBTASK_GATE)
        self.assertEqual(self.app.audio.alert_state(), "playing")
        self.assertTrue(today.focus_view.gate_button.get_mapped())

        self.click(self.window.alert_bar_button)
        pump(0.3)

        self.assertIsInstance(self.window.alert_dialog, SubtaskGateDialog)
        self.assertTrue(self.window.alert_dialog.complete_button.get_mapped())
        self.assertFalse(self.window.alert_bar.get_visible())

    def rest_over(self, **overrides):
        today, target = self.start_short_target(**overrides)
        self.click(today.focus_view.complete_button)
        self.advance(60)
        return today, target

    def test_a_finished_break_waits_for_the_user_with_a_looping_sound(self):
        today, _target = self.rest_over(snooze_limit=1)

        dialog = self.window.alert_dialog
        self.assertIsInstance(dialog, NextBatchDialog)
        self.assertEqual(self.phase, TargetPhase.READY_FOR_NEXT)
        self.assertTrue(self.audio_playing())
        self.assertEqual(dialog.heading_label.get_text(), "Break is over")
        self.assertEqual(dialog.batch_label.get_text(), "Batch 2 of 2")
        self.assertEqual(dialog.subtask_label.get_text(), "Second step")
        self.assertEqual(text_of(dialog.start_button), "Start Batch 2")
        self.assertEqual(dialog.extend_status.get_text(), "1 of 1 extensions left")
        self.advance(10 * 60)
        self.assertEqual(self.phase, TargetPhase.READY_FOR_NEXT)
        focus = today.focus_view
        self.assertEqual(focus.ring.text, "00:00")
        self.assertIn("Batch 2", self.window.live_label.get_text())

        self.click(dialog.start_button)

        state = self.scheduler.session.state
        self.assertEqual((state.phase, state.batch_index), (TargetPhase.WORKING, 2))
        self.assertIsNone(self.window.alert_dialog)
        self.assertEqual(self.app.audio.alert_state(), "null")
        self.assertEqual(focus.ring.text, "01:00")

    def test_resting_a_little_longer_counts_against_the_snooze_limit(self):
        self.rest_over(snooze_limit=1)
        dialog = self.window.alert_dialog

        self.click(dialog.extend_button)

        self.assertEqual(self.phase, TargetPhase.RESTING)
        self.assertEqual(self.scheduler.session.state.remaining_ms, 5 * 60_000)
        self.assertIsNone(self.window.alert_dialog)
        self.advance(5 * 60)
        dialog = self.window.alert_dialog
        self.assertIsInstance(dialog, NextBatchDialog)
        self.assertFalse(dialog.extend_button.is_sensitive())
        self.assertEqual(dialog.extend_status.get_text(), "No more extensions. Start the next batch or quit.")

    def test_escape_on_the_ready_prompt_keeps_the_sound_and_an_alert_bar(self):
        today, _target = self.rest_over()

        self.press_escape(self.window.alert_dialog)

        self.assertTrue(self.window.alert_bar.get_mapped())
        self.assertEqual(self.window.alert_bar_label.get_text(), "Break is over: Second step")
        self.assertEqual(self.app.audio.alert_state(), "playing")
        focus = today.focus_view
        self.assertTrue(focus.ready_button.get_mapped())
        self.assertEqual(text_of(focus.ready_button), "Start Batch 2")
        self.click(focus.ready_button)
        self.assertEqual(self.phase, TargetPhase.WORKING)
        self.assertFalse(self.window.alert_bar.get_visible())

    def test_ready_prompt_buttons_work_from_the_notification(self):
        self.rest_over()
        self.assertEqual(self.notifier.button_labels("target-ready"), ["Start Batch 2", "Rest 5 min"])

        self.app.activate_action("ready-extend", GLib.Variant("i", 5))
        pump(0.2)
        self.assertEqual(self.phase, TargetPhase.RESTING)
        self.advance(5 * 60)
        self.app.activate_action("ready-start", None)
        pump(0.2)

        self.assertEqual((self.phase, self.scheduler.session.state.batch_index), (TargetPhase.WORKING, 2))

    def test_checking_during_a_break_asks_before_ending_it(self):
        today, _target = self.start_short_target()
        focus = today.focus_view
        self.click(focus.complete_button)
        self.advance(20)

        self.click(focus.complete_button)
        confirm = self.window.last_confirm
        self.assertEqual(confirm.get_heading(), "Start Batch 2 now?")
        self.respond(confirm, "cancel")
        self.assertEqual(self.phase, TargetPhase.RESTING)
        self.assertEqual(self.scheduler.session.state.remaining_ms, 40_000)

        self.click(focus.complete_button)
        self.respond(self.window.last_confirm, "confirm")

        self.assertEqual((self.phase, self.scheduler.session.state.batch_index), (TargetPhase.WORKING, 2))
        self.assertEqual(focus.complete_button.get_tooltip_text(), "Complete subtask")

    def test_timeline_spine_runs_unbroken_from_top_to_bottom(self):
        today, _target = self.start_short_target()
        nodes = today.focus_view.timeline.nodes
        self.assertEqual(len(nodes), 4)

        rows = today.focus_view.timeline.rows
        previous = None
        for node in nodes:
            ok, bounds = node.compute_bounds(rows)
            self.assertTrue(ok)
            if previous is not None:
                self.assertAlmostEqual(bounds.get_y(), previous.get_y() + previous.get_height(), delta=0.5)
            previous = bounds

    def test_pause_resume_and_quit_keep_progress(self):
        today, target = self.start_short_target()
        focus = today.focus_view
        self.advance(20)

        self.click(focus.pause_button)
        self.assertEqual(self.phase, TargetPhase.PAUSED_WORK)
        self.assertEqual(focus.pause_button.get_tooltip_text(), "Resume")
        self.advance(300)
        self.assertEqual(focus.ring.text, "00:40")
        self.click(focus.pause_button)
        self.assertEqual(self.phase, TargetPhase.WORKING)
        self.click(focus.quit_button)
        self.respond(self.window.last_confirm, "confirm")

        self.assertIsNone(self.scheduler.session)
        self.assertEqual(today.pages.get_visible_child_name(), "list")
        saved = self.repo.get_target(target.id)
        self.assertEqual((saved.status, saved.batches[0].actual_work_seconds), ("in_progress", 20))
        self.assertFalse(self.window.live_pill.get_visible())

    def test_cancelling_the_quit_confirmation_keeps_the_session(self):
        today, _target = self.start_short_target()

        self.click(today.focus_view.quit_button)
        self.respond(self.window.last_confirm, "cancel")

        self.assertEqual(self.phase, TargetPhase.WORKING)

    def test_names_containing_markup_are_shown_as_plain_text(self):
        name = '<b>Bold</b> & <span foreground="red">x</span>'
        subtask = "<i>italic</i> <unclosed & more"

        today, target = self.start_short_target(name=name, subtask_titles=(subtask, "plain"))
        focus = today.focus_view
        self.assertEqual(focus.title_label.get_text(), name)
        self.assertFalse(focus.title_label.get_use_markup())
        self.assertEqual(focus.subtask_label.get_text(), subtask)
        self.assertTrue(self.window.live_label.get_text().startswith(name))
        self.assertFalse(self.window.live_label.get_use_markup())
        self.advance(60)
        gate = self.window.alert_dialog
        self.assertIn(subtask, gate.prompt_label.get_text())
        self.assertFalse(gate.prompt_label.get_use_markup())
        self.press_escape(gate)

        self.assertEqual(self.window.alert_bar_label.get_text(), f"Waiting for check-off: {subtask}")
        self.click(today.focus_view.back_button)
        self.assertEqual(today.rows[target.id]["name"].get_text(), name)
        self.assertFalse(today.rows[target.id]["name"].get_use_markup())
        self.window.toast(name)
        pump(0.1)

    def test_target_can_be_edited_and_deleted(self):
        target = self.repo.create_target(EXAMPLE_1)
        today = self.window.view("today")
        today.reload()
        pump(0.2)

        self.click(today.rows[target.id]["edit"])
        pump(0.3)
        dialog = today.editor
        self.assertEqual(dialog.name_entry.get_text(), "Website Profile Company")
        self.assertEqual(dialog.timeline.total_text, "Total Timeline: 6h 40m")
        self.assertEqual(dialog.subtask_entries[2].get_text(), "Coding backend")
        dialog.name_entry.set_text("Company Site v2")
        self.click(dialog.save_button)
        pump(0.3)
        self.assertEqual(self.repo.get_target(target.id).name, "Company Site v2")
        self.assertEqual(today.rows[target.id]["name"].get_text(), "Company Site v2")

        self.click(today.rows[target.id]["delete"])
        self.respond(self.window.last_confirm, "confirm")
        pump(0.3)

        self.assertEqual(self.repo.list_targets(), [])
        self.assertEqual(today.states.state, "empty")

    def test_unfinished_target_is_offered_for_resume_after_a_restart(self):
        _today, target = self.start_short_target()
        self.advance(25)
        self.close_app()

        self.launch()

        today = self.window.view("today")
        self.assertEqual(self.scheduler.session.target.id, target.id)
        self.assertEqual(self.phase, TargetPhase.PAUSED_WORK)
        self.assertEqual(text_of(today.hero_button), "Resume Focus")
        self.assertEqual(today.hero_time.get_text(), "00:00:35")
        self.click(today.hero_button)
        self.assertEqual(self.phase, TargetPhase.WORKING)


class TimelineClockTests(GtkAppTestCase):
    """The timeline can show minutes from the start, or the local clock time of every block."""

    def setUp(self):
        super().setUp()
        # The desktop's 12 or 24 hour setting differs per machine; these checks use 24 hours.
        patcher = mock.patch.object(vertical_timeline, "desktop_uses_24h", lambda: True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_create_page_shows_when_each_block_ends_by_the_clock(self):
        _today, editor = self.open_editor("today")
        editor.hours_spin.set_value(1)
        self.choose(editor.pomodoro_group, "25/5")
        timeline = editor.timeline
        self.assertEqual(timeline.entries()[0][2], "00:00 -> 00:25")

        self.choose(timeline.mode_group, "clock")

        self.assertEqual(
            [entry[2] for entry in timeline.entries()],
            ["08:00 -> 08:25", "08:25 -> 08:30", "08:30 -> 08:55", "08:55 -> 09:00"],
        )
        self.assertEqual(timeline.total_text, "Total Timeline: 1h, ends 09:00")
        self.assertEqual(self.repo.get_pref("timeline_clock", "0"), "1")

    def test_clock_times_follow_the_live_session_and_the_saved_choice(self):
        self.repo.set_pref("timeline_clock", "1")
        target = self.repo.create_target(EXAMPLE_1)
        today = self.window.view("today")
        today.reload()
        pump(0.2)
        self.click(today.rows[target.id]["play"])
        pump(0.2)
        timeline = today.focus_view.timeline
        self.assertEqual(timeline.mode_group.get_value(), "clock")

        self.advance(10 * 60)
        self.assertEqual(timeline.entries()[0][2], "08:00 -> 08:50")
        self.click(today.focus_view.pause_button)
        self.advance(5 * 60)

        self.assertEqual(timeline.entries()[0][2], "08:05 -> 08:55", "a pause pushes the plan later")
        self.choose(timeline.mode_group, "duration")
        self.assertEqual(timeline.entries()[0][2], "00:00 -> 00:50")


class FocusLayoutTests(GtkAppTestCase):
    def focus(self):
        target = self.repo.create_target(EXAMPLE_1)
        self.scheduler.start_target(target.id)
        today = self.window.view("today")
        today.show_focus()
        pump(0.4)
        return today.focus_view

    def test_the_ring_is_larger_than_before_in_a_normal_window(self):
        focus = self.focus()

        self.assertGreaterEqual(target_size(focus.ring)[0], 320)
        self.assertGreaterEqual(focus.ring.text_px, 64)


class FullScreenFocusTests(FocusLayoutTests):
    window_size = (1900, 1040)

    def test_a_full_screen_window_grows_the_ring_and_timeline(self):
        focus = self.focus()

        self.assertGreaterEqual(target_size(focus.ring)[0], 440)
        self.assertGreaterEqual(focus.ring.text_px, 88)
        self.assertTrue(self.window.has_css_class("large"))
        ok, right = focus.right.compute_bounds(self.window)
        self.assertTrue(ok)
        self.assertGreater(right.get_width(), 600, "the timeline uses the extra width")


class CompactFocusTests(FocusLayoutTests):
    window_size = (420, 800)

    def test_a_narrow_window_keeps_a_fitting_ring(self):
        focus = self.focus()

        width = target_size(focus.ring)[0]
        self.assertGreaterEqual(width, 240)
        self.assertLessEqual(width, 388)
        self.assertLessEqual(self.window.get_content().measure(Gtk.Orientation.HORIZONTAL, -1)[0], 420)


class AlarmFlowTests(GtkAppTestCase):
    def open_dialog(self):
        return self.open_editor("alarm")

    def ring(self, **overrides):
        alarm = self.repo.create_alarm(alarm_draft(**overrides))
        self.scheduler.refresh_alarms()
        self.advance(300)
        dialog = self.window.alert_dialog
        self.assertIsInstance(dialog, AlarmRingingDialog)
        self.assertTrue(dialog.snooze_button.get_mapped())
        return alarm, dialog

    def set_snooze(self, editor, interval, limit):
        sheet = self.open_sheet(editor.snooze_row)
        for key, value in (("interval", interval), ("limit", limit)):
            group = sheet.groups[key]
            if not group.button_for(value).get_mapped():
                self.click(group.more_button)
            self.choose(group, value)
        self.click(sheet.save_button)
        pump(0.2)

    def set_mission(self, editor, mode, count=1, difficulty="easy"):
        sheet = self.open_sheet(editor.mission_slot)
        self.choose(sheet.groups["mission"], mode)
        if mode != "off":
            self.choose(sheet.groups["count"], count)
        if mode == "math":
            self.choose(sheet.groups["difficulty"], difficulty)
        self.click(sheet.save_button)
        pump(0.2)

    def test_new_alarm_dialog_saves_time_repeat_sound_snooze_and_mission(self):
        alarms, dialog = self.open_dialog()
        self.assertEqual(dialog.title_label.get_text(), "New Alarm")
        self.assertEqual(len(dialog.sound_picker.sound_names()), 6)
        self.assertEqual(sorted(dialog.day_buttons), [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(dialog.repeat_summary.get_text(), "Once")

        dialog.hour_spin.set_value(8)
        dialog.minute_spin.set_value(5)
        self.choose(dialog.period_group, "AM")
        for day in (1, 2, 3, 4, 5):
            self.click(dialog.day_buttons[day])
        self.assertEqual(dialog.selected_days(), (1, 2, 3, 4, 5))
        self.assertEqual(dialog.repeat_summary.get_text(), "Weekdays")
        dialog.name_entry.set_text("Morning class")
        dialog.sound_picker.set_sound_id("bundled-funny")
        self.assertEqual(text_of(dialog.snooze_row), "Snooze 5 min, 3 times")
        self.set_snooze(dialog, 10, 2)
        self.assertEqual(text_of(dialog.snooze_row), "Snooze 10 min, 2 times")
        self.set_mission(dialog, "math", 3, "hard")
        self.assertEqual(dialog.mission_slot.icon.get_icon_name(), "productime-math-symbolic")
        self.assertIn("Hard", dialog.mission_slot.get_tooltip_text())
        self.click(dialog.save_button)
        pump(0.3)

        alarm = self.repo.list_alarms()[0]
        self.assertEqual((alarm.hour_24, alarm.minute, alarm.repeat_days), (8, 5, (1, 2, 3, 4, 5)))
        self.assertEqual(
            (alarm.sound_id, alarm.snooze_interval_minutes, alarm.snooze_limit), ("bundled-funny", 10, 2)
        )
        self.assertEqual(
            (alarm.mission_mode, alarm.mission_count, alarm.mission_difficulty), ("math", 3, "hard")
        )
        card = alarms.cards[alarm.id]
        self.assertEqual(card["time"].get_text(), "08:05 AM")
        self.assertEqual(card["name"].get_text(), "Morning class")
        self.assertEqual(card["repeat"].get_text(), "Weekdays")
        self.assertEqual(card["badges"], ["Funny", "Math Hard 3x"])
        self.assertEqual(card["next"].get_text(), "Rings in 5m")
        self.assertEqual(alarms.summary_label.get_text(), "Next alarm: Morning class, in 5m")

    def test_mission_slot_is_one_dashed_box_with_a_plus_until_a_mission_is_chosen(self):
        _alarms, dialog = self.open_dialog()
        slot = dialog.mission_slot

        self.assertTrue(slot.has_css_class("mission-slot"))
        self.assertEqual(slot.icon.get_icon_name(), "list-add-symbolic")
        self.assertEqual(slot.get_tooltip_text(), "Add a wake-up mission")
        self.assertGreaterEqual(min(target_size(slot)), 88)

        self.set_mission(dialog, "typing", 2)
        self.assertEqual(slot.icon.get_icon_name(), "productime-typing-symbolic")
        self.assertIn("2 phrases", slot.get_tooltip_text())
        self.set_mission(dialog, "off")
        self.assertEqual(slot.icon.get_icon_name(), "list-add-symbolic")

    def test_mission_sheet_offers_three_icon_options_with_explanations(self):
        _alarms, dialog = self.open_dialog()
        sheet = self.open_sheet(dialog.mission_slot)
        modes = sheet.groups["mission"]

        icons = {}
        for mode in ("off", "typing", "math"):
            button = modes.button_for(mode)
            self.assertTrue(button.get_mapped(), mode)
            self.assertTrue(button.get_tooltip_text(), mode)
            icons[mode] = self.images_under(button)[0].get_icon_name()
        self.assertEqual(
            icons,
            {
                "off": "productime-mission-off-symbolic",
                "typing": "productime-typing-symbolic",
                "math": "productime-math-symbolic",
            },
        )
        self.assertIn("10 phrases", modes.button_for("typing").get_tooltip_text())
        self.assertFalse(sheet.groups["count"].get_mapped())

        self.choose(modes, "typing")
        self.assertTrue(sheet.groups["count"].get_mapped())
        self.assertFalse(sheet.groups["difficulty"].get_mapped())
        self.choose(modes, "math")
        self.assertTrue(sheet.groups["difficulty"].get_mapped())
        self.assertIn("Hard", sheet.groups["difficulty"].button_for("hard").get_tooltip_text())

        self.click(sheet.cancel_button)
        pump(0.2)
        self.assertEqual(dialog.mission_slot.icon.get_icon_name(), "list-add-symbolic")

    def test_count_and_difficulty_are_picture_tiles(self):
        _alarms, dialog = self.open_dialog()
        sheet = self.open_sheet(dialog.mission_slot)
        count, difficulty = sheet.groups["count"], sheet.groups["difficulty"]

        self.choose(sheet.groups["mission"], "typing")
        for n in (1, 2, 3):
            icons = [image.get_icon_name() for image in self.images_under(count.button_for(n))]
            self.assertEqual(icons, ["productime-typing-symbolic"] * n)
        self.assertEqual(sheet.section_values["count"].get_text(), "1 phrase")
        self.choose(sheet.groups["mission"], "math")
        self.choose(count, 3)
        self.assertEqual(
            {image.get_icon_name() for image in self.images_under(count.button_for(2))},
            {"productime-math-symbolic"},
        )
        self.assertEqual(sheet.section_values["count"].get_text(), "3 problems")

        levels = (("easy", 1, "24 + 17"), ("medium", 2, "13 \u00d7 7"), ("hard", 3, "18 \u00d7 12 + 90"))
        for level, bars, sample in levels:
            tile = difficulty.button_for(level)
            lit = [
                box
                for box in self.widgets_under(tile, Gtk.Box)
                if box.has_css_class("meter-bar") and box.has_css_class("on")
            ]
            self.assertEqual(len(lit), bars, level)
            self.assertIn(sample, [label.get_text() for label in self.labels_under(tile)])
            self.assertTrue(tile.has_css_class(f"option-{level}"), level)

    def test_click_mission_has_presets_a_custom_count_and_hides_other_settings(self):
        alarms, dialog = self.open_dialog()
        sheet = self.open_sheet(dialog.mission_slot)
        modes = sheet.groups["mission"]
        self.assertEqual(
            self.images_under(modes.button_for("click"))[0].get_icon_name(), "productime-click-symbolic"
        )
        self.assertIn("Click", modes.button_for("click").get_tooltip_text())

        self.choose(modes, "click")

        self.assertTrue(sheet.groups["clicks"].get_mapped())
        self.assertFalse(sheet.groups["count"].get_mapped())
        self.assertFalse(sheet.groups["difficulty"].get_mapped())
        for value in (20, 30, 50, "custom"):
            self.assertTrue(sheet.groups["clicks"].button_for(value).get_mapped(), value)
        self.assertEqual(sheet.groups["clicks"].get_value(), 30)
        self.assertFalse(sheet.clicks_spin.get_mapped())
        self.choose(sheet.groups["clicks"], "custom")
        self.assertTrue(sheet.clicks_spin.get_mapped())
        sheet.clicks_spin.set_value(75)
        self.assertEqual(sheet.section_values["clicks"].get_text(), "75 clicks")
        self.click(sheet.save_button)
        pump(0.2)

        self.assertEqual(dialog.mission_slot.icon.get_icon_name(), "productime-click-symbolic")
        self.assertEqual(dialog.mission_slot.caption.get_text(), "75")
        self.assertIn("75 times", dialog.mission_slot.get_tooltip_text())
        dialog.name_entry.set_text("Bangun")
        self.click(dialog.save_button)
        pump(0.3)
        alarm = self.repo.list_alarms()[0]
        self.assertEqual((alarm.mission_mode, alarm.mission_clicks), ("click", 75))
        self.assertIn("Click 75x", alarms.cards[alarm.id]["badges"])

    def test_click_mission_counts_presses_and_stops_the_alarm_at_the_target(self):
        _alarm, dialog = self.ring(mission_mode="click", mission_clicks=10)
        button = dialog.click_button
        self.assertTrue(button.get_mapped())
        self.assertGreaterEqual(min(target_size(button)), 120)
        self.assertEqual(dialog.click_counter.get_text(), "0 / 10")
        self.assertFalse(dialog.stop_button.get_visible())

        for _ in range(9):
            self.click(button)
        self.assertEqual(dialog.click_counter.get_text(), "9 / 10")
        self.assertEqual(self.app.audio.alert_state(), "playing")
        self.click(button)

        self.assertEqual(self.scheduler.ringing, [])
        self.assertIsNone(self.window.alert_dialog)

    def test_holding_a_key_on_the_click_button_counts_once(self):
        _alarm, dialog = self.ring(mission_mode="click", mission_clicks=10)
        guard = dialog.key_guard

        self.assertFalse(guard.emit("key-pressed", Gdk.KEY_space, 65, 0))
        self.assertTrue(guard.emit("key-pressed", Gdk.KEY_space, 65, 0), "auto-repeat is swallowed")
        guard.emit("key-released", Gdk.KEY_space, 65, 0)
        self.assertFalse(guard.emit("key-pressed", Gdk.KEY_Return, 36, 0))

    def test_snooze_sheet_shows_three_choices_and_more_options(self):
        _alarms, dialog = self.open_dialog()
        sheet = self.open_sheet(dialog.snooze_row)
        interval, limit = sheet.groups["interval"], sheet.groups["limit"]

        self.assertEqual(sheet.section_values["interval"].get_text(), "5 minutes")
        self.assertEqual(sheet.section_values["limit"].get_text(), "3 times")
        for value in (1, 5, 10):
            self.assertTrue(interval.button_for(value).get_mapped(), value)
        self.assertFalse(interval.button_for(15).get_mapped())
        self.assertFalse(limit.button_for(5).get_mapped())
        self.click(interval.more_button)
        for value in (15, 20, 25, 30):
            self.assertTrue(interval.button_for(value).get_mapped(), value)
        self.assertFalse(interval.more_button.get_visible())

        self.choose(interval, 25)
        self.assertEqual(sheet.section_values["interval"].get_text(), "25 minutes")
        self.press_escape(sheet)
        self.assertEqual(text_of(dialog.snooze_row), "Snooze 5 min, 3 times")

    def test_snooze_sheet_opens_expanded_when_the_saved_value_is_a_longer_option(self):
        alarm = self.repo.create_alarm(alarm_draft(snooze_interval_minutes=20, snooze_limit=10))
        alarms = self.go("alarm")
        self.click(alarms.cards[alarm.id]["edit"])
        pump(0.3)
        dialog = alarms.editor
        self.assertEqual(text_of(dialog.snooze_row), "Snooze 20 min, 10 times")

        sheet = self.open_sheet(dialog.snooze_row)

        self.assertTrue(sheet.groups["interval"].button_for(20).get_active())
        self.assertTrue(sheet.groups["interval"].button_for(20).get_mapped())
        self.assertTrue(sheet.groups["limit"].button_for(10).get_mapped())

    def test_every_day_check_selects_all_seven_days(self):
        _alarms, dialog = self.open_dialog()

        self.toggle(dialog.every_day_check)
        self.assertEqual(dialog.selected_days(), (1, 2, 3, 4, 5, 6, 7))
        self.assertEqual(dialog.repeat_summary.get_text(), "Every day")
        self.click(dialog.day_buttons[7])
        self.assertFalse(dialog.every_day_check.get_active())
        self.assertEqual(dialog.selected_days(), (1, 2, 3, 4, 5, 6))
        self.click(dialog.day_buttons[7])
        self.assertTrue(dialog.every_day_check.get_active())
        self.toggle(dialog.every_day_check)
        self.assertEqual(dialog.selected_days(), ())
        self.assertEqual(dialog.repeat_summary.get_text(), "Once")
        self.assertIn("next matching time", dialog.repeat_summary.get_tooltip_text())

    def test_am_pm_entry_is_stored_as_24_hour_time(self):
        cases = (
            (12, "AM", 0, "12:30 AM"),
            (12, "PM", 12, "12:30 PM"),
            (1, "PM", 13, "01:30 PM"),
            (11, "PM", 23, "11:30 PM"),
        )
        for hour, period, hour_24, shown in cases:
            alarms, dialog = self.open_dialog()
            dialog.hour_spin.set_value(hour)
            dialog.minute_spin.set_value(30)
            self.choose(dialog.period_group, period)
            dialog.name_entry.set_text(f"Alarm {hour} {period}")
            self.click(dialog.save_button)
            pump(0.3)

            alarm = next(a for a in self.repo.list_alarms() if a.name == f"Alarm {hour} {period}")
            self.assertEqual(alarm.hour_24, hour_24)
            self.assertEqual(alarms.cards[alarm.id]["time"].get_text(), shown)

    def test_alarm_without_a_name_is_rejected_inline(self):
        _alarms, dialog = self.open_dialog()

        self.click(dialog.save_button)

        self.assertEqual(dialog.error_label.get_text(), "Alarm name: This field cannot be empty.")
        self.assertEqual(self.repo.list_alarms(), [])

    def test_typing_mission_blocks_pasting_and_needs_every_phrase(self):
        _alarm, dialog = self.ring(mission_mode="typing", mission_count=2)
        entry = self.scheduler.ringing[0]
        self.assertTrue(self.audio_playing())
        self.assertEqual(dialog.name_label.get_text(), "Morning class")
        self.assertEqual(dialog.time_label.get_text(), "08:05 AM")
        self.assertEqual(dialog.prompt_label.get_text(), entry.prompts[0])
        self.assertEqual(dialog.counter_label.get_text(), "1/2")
        self.assertFalse(dialog.stop_button.get_visible())
        self.assertFalse(dialog.prompt_label.get_selectable())

        dialog.answer_entry.get_delegate().insert_text(entry.prompts[0], 0)
        pump(0.05)
        self.assertEqual(dialog.answer_entry.get_text(), "")
        self.assertEqual(dialog.feedback_label.get_text(), PASTE_BLOCKED)
        dialog.answer_entry.set_text(entry.prompts[0])
        pump(0.05)
        self.assertEqual(dialog.answer_entry.get_text(), "")
        self.click(dialog.submit_button)
        self.assertEqual(entry.mission_index, 0)

        self.type_text(dialog.answer_entry, "something else")
        self.click(dialog.submit_button)
        self.assertEqual(dialog.feedback_label.get_text(), "Not correct yet. Check it and try again.")
        self.assertEqual(self.app.audio.alert_state(), "playing")
        dialog.answer_entry.set_text("")

        self.type_text(dialog.answer_entry, entry.prompts[0])
        self.assertEqual(dialog.answer_entry.get_text(), entry.prompts[0])
        self.click(dialog.submit_button)
        self.assertEqual(dialog.counter_label.get_text(), "2/2")
        self.assertEqual(dialog.prompt_label.get_text(), entry.prompts[1])
        self.assertEqual(dialog.answer_entry.get_text(), "")
        self.assertEqual(self.app.audio.alert_state(), "playing")
        self.type_text(dialog.answer_entry, entry.prompts[1])
        dialog.answer_entry.emit("activate")
        pump(0.2)

        self.assertEqual(self.scheduler.ringing, [])
        self.assertIsNone(self.window.alert_dialog)
        self.assertEqual(self.app.audio.alert_state(), "null")

    def test_math_mission_keeps_ringing_until_every_answer_is_right(self):
        _alarm, dialog = self.ring(mission_mode="math", mission_count=2, mission_difficulty="hard")
        entry = self.scheduler.ringing[0]
        self.assertEqual(dialog.prompt_label.get_text(), f"{entry.problems[0].text} = ?")

        self.type_text(dialog.answer_entry, str(entry.problems[0].answer + 1))
        self.click(dialog.submit_button)
        self.assertEqual(entry.mission_index, 0)
        self.assertEqual(dialog.feedback_label.get_text(), "Not correct yet. Check it and try again.")
        dialog.answer_entry.set_text("")
        self.type_text(dialog.answer_entry, str(entry.problems[0].answer))
        self.click(dialog.submit_button)
        self.assertEqual(dialog.counter_label.get_text(), "2/2")
        self.assertEqual(self.app.audio.alert_state(), "playing")
        self.type_text(dialog.answer_entry, str(entry.problems[1].answer))
        self.click(dialog.submit_button)
        pump(0.2)

        self.assertEqual(self.scheduler.ringing, [])
        self.assertEqual(self.app.audio.alert_state(), "null")

    def test_snooze_rings_again_and_locks_at_the_limit(self):
        alarm, dialog = self.ring(snooze_limit=1, snooze_interval_minutes=5)
        self.assertEqual(text_of(dialog.snooze_button), "Snooze 5 min")
        self.assertEqual(dialog.snooze_status.get_text(), "1 of 1 snoozes left")
        self.assertTrue(dialog.stop_button.get_mapped())

        self.click(dialog.snooze_button)
        pump(0.2)
        self.assertEqual(self.scheduler.ringing, [])
        self.assertIn(alarm.id, self.scheduler.snoozed)
        self.assertIsNone(self.window.alert_dialog)
        self.assertEqual(self.app.audio.alert_state(), "null")
        self.advance(300)

        dialog = self.window.alert_dialog
        self.assertIsInstance(dialog, AlarmRingingDialog)
        self.assertFalse(dialog.snooze_button.is_sensitive())
        self.assertEqual(dialog.snooze_status.get_text(), ALARM_LIMIT_REACHED)
        self.assertTrue(self.audio_playing())
        self.click(dialog.stop_button)
        pump(0.2)

        self.assertEqual(self.app.audio.alert_state(), "null")
        self.assertIsNone(self.window.alert_dialog)

    def test_escape_on_a_ringing_alarm_keeps_it_ringing_and_locked(self):
        alarm, dialog = self.ring(mission_mode="typing", repeat_days=(1,))
        self.assertTrue(self.audio_playing())

        self.press_escape(dialog)

        self.assertEqual(self.window.alert_bar_label.get_text(), "Alarm ringing: Morning class")
        self.assertTrue(self.window.alert_bar.get_mapped())
        self.assertEqual(len(self.scheduler.ringing), 1)
        self.assertEqual(self.app.audio.alert_state(), "playing")
        alarms = self.go("alarm")
        card = alarms.cards[alarm.id]
        for control in ("edit", "delete", "switch"):
            self.assertFalse(card[control].is_sensitive(), control)
        self.assertIn("Ringing", card["badges"])
        alarms.delete(alarm.id)
        self.assertIsNotNone(self.repo.get_alarm(alarm.id))

        self.click(self.window.alert_bar_button)
        pump(0.3)
        self.assertIsInstance(self.window.alert_dialog, AlarmRingingDialog)

    def test_switch_turns_an_alarm_off_so_it_no_longer_rings(self):
        alarm = self.repo.create_alarm(alarm_draft(repeat_days=(1, 2, 3, 4, 5, 6, 7)))
        self.scheduler.refresh_alarms()
        alarms = self.go("alarm")

        alarms.cards[alarm.id]["switch"].set_active(False)
        pump(0.3)

        self.assertFalse(self.repo.get_alarm(alarm.id).enabled)
        self.assertEqual(alarms.cards[alarm.id]["next"].get_text(), "Off")
        self.assertEqual(alarms.summary_label.get_text(), "No active alarms.")
        self.advance(600)
        self.assertEqual(self.scheduler.ringing, [])

    def test_alarm_can_be_edited_and_deleted(self):
        alarm = self.repo.create_alarm(alarm_draft(repeat_days=(6, 7)))
        self.scheduler.refresh_alarms()
        alarms = self.go("alarm")

        self.click(alarms.cards[alarm.id]["edit"])
        pump(0.3)
        dialog = alarms.editor
        self.assertEqual(dialog.name_entry.get_text(), "Morning class")
        self.assertEqual(dialog.selected_days(), (6, 7))
        self.assertEqual((dialog.hour_spin.get_value_as_int(), dialog.period_group.get_value()), (8, "AM"))
        dialog.name_entry.set_text("Weekend run")
        self.toggle(dialog.every_day_check)
        self.click(dialog.save_button)
        pump(0.3)
        self.assertEqual(self.repo.get_alarm(alarm.id).name, "Weekend run")
        self.assertEqual(alarms.cards[alarm.id]["repeat"].get_text(), "Every day")

        self.click(alarms.cards[alarm.id]["delete"])
        self.respond(self.window.last_confirm, "confirm")
        pump(0.3)

        self.assertEqual(self.repo.list_alarms(), [])
        self.assertEqual(alarms.states.state, "empty")


class ClockStylePickerTests(GtkAppTestCase):
    """Number fields follow GNOME Clocks: plus above, the value in the middle, minus below."""

    def spin_buttons(self, widget):
        found, stack = [], [widget]
        while stack:
            current = stack.pop()
            if isinstance(current, Gtk.SpinButton) and current.get_mapped():
                found.append(current)
            child = current.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return found

    def assert_clock_style(self, spin_button):
        self.assertEqual(spin_button.get_orientation(), Gtk.Orientation.VERTICAL)
        parts = {}
        child = spin_button.get_first_child()
        while child is not None:
            name = child.get_css_name()
            if name == "button":
                parts["up" if child.has_css_class("up") else "down"] = child
            elif name == "text":
                parts["text"] = child
            child = child.get_next_sibling()
        tops = {}
        for key, part in parts.items():
            ok, bounds = part.compute_bounds(spin_button)
            self.assertTrue(ok)
            tops[key] = bounds.get_y()
            if key != "text":
                self.assertGreaterEqual(bounds.get_height(), 44, key)
                self.assertGreaterEqual(bounds.get_width(), 44, key)
        self.assertLess(tops["up"], tops["text"])
        self.assertLess(tops["text"], tops["down"])

    def test_target_duration_and_plan_numbers_use_clock_style_pickers(self):
        _today, dialog = self.open_editor("today")

        spins = self.spin_buttons(dialog)
        self.assertGreaterEqual(len(spins), 2)
        sheet = self.open_sheet(dialog.pomodoro_group.button_for("custom"))
        spins += self.spin_buttons(sheet)
        self.assertGreaterEqual(len(spins), 4)
        for spin_button in spins:
            self.assert_clock_style(spin_button)
        for spin_button in (dialog.hours_spin, dialog.minutes_spin):
            self.assertTrue(spin_button.has_css_class("big"))
        self.assertEqual(dialog.minutes_spin.get_text(), "00")

    def test_alarm_time_reads_like_a_clock_with_am_pm_beside_it(self):
        _alarms, dialog = self.open_editor("alarm")

        for spin_button in (dialog.hour_spin, dialog.minute_spin):
            self.assert_clock_style(spin_button)
            self.assertTrue(spin_button.has_css_class("big"))
        ok_hour, hour = dialog.hour_spin.compute_bounds(dialog)
        ok_minute, minute = dialog.minute_spin.compute_bounds(dialog)
        ok_period, period = dialog.period_group.compute_bounds(dialog)
        self.assertTrue(ok_hour and ok_minute and ok_period)
        self.assertLess(hour.get_x(), minute.get_x())
        self.assertLess(minute.get_x(), period.get_x())
        self.assertAlmostEqual(hour.get_y(), minute.get_y(), delta=1)
        self.assertEqual(dialog.hour_spin.get_text(), "07")

        self.click_spin(dialog.minute_spin, "up")
        self.assertEqual(dialog.minute_spin.get_value_as_int(), 1)
        self.click_spin(dialog.hour_spin, "down")
        self.assertEqual(dialog.hour_spin.get_value_as_int(), 6)

    def center_x(self, widget, ancestor) -> float:
        ok, bounds = widget.compute_bounds(ancestor)
        self.assertTrue(ok)
        return bounds.get_x() + bounds.get_width() / 2

    def test_alarm_page_is_centred_with_a_vertical_am_pm_switch(self):
        _alarms, dialog = self.open_editor("alarm")
        middle = dialog.body.get_width() / 2

        self.assertAlmostEqual(self.center_x(dialog.time_grid, dialog.body), middle, delta=2)
        ok_am, am = dialog.period_group.button_for("AM").compute_bounds(dialog.body)
        ok_pm, pm = dialog.period_group.button_for("PM").compute_bounds(dialog.body)
        self.assertTrue(ok_am and ok_pm)
        self.assertAlmostEqual(am.get_x(), pm.get_x(), delta=1)
        self.assertLess(am.get_y(), pm.get_y())

        day_tops = set()
        for button in dialog.day_buttons.values():
            ok, bounds = button.compute_bounds(dialog.body)
            self.assertTrue(ok)
            day_tops.add(round(bounds.get_y()))
            self.assertGreaterEqual(min(bounds.get_width(), bounds.get_height()), 44)
        self.assertEqual(len(day_tops), 1, "all seven days should sit on one row")
        self.assertAlmostEqual(self.center_x(dialog.days_box, dialog.body), middle, delta=2)
        ok_summary, summary = dialog.repeat_summary.compute_bounds(dialog.body)
        ok_check, check = dialog.every_day_check.compute_bounds(dialog.body)
        self.assertTrue(ok_summary and ok_check)
        self.assertLess(summary.get_x(), check.get_x())
        self.assertAlmostEqual(
            summary.get_y() + summary.get_height() / 2, check.get_y() + check.get_height() / 2, delta=4
        )

    def test_timer_picker_is_centred(self):
        _timers, dialog = self.open_editor("timer")

        self.assertAlmostEqual(
            self.center_x(dialog.time_grid, dialog.body), dialog.body.get_width() / 2, delta=2
        )

    def test_timer_duration_uses_three_clock_style_pickers(self):
        _timers, dialog = self.open_editor("timer")

        for spin_button in (dialog.hours_spin, dialog.minutes_spin, dialog.seconds_spin):
            self.assert_clock_style(spin_button)
            self.assertTrue(spin_button.has_css_class("big"))
        self.assertEqual(
            [s.get_text() for s in (dialog.hours_spin, dialog.minutes_spin, dialog.seconds_spin)],
            ["00", "05", "00"],
        )

    def test_pickers_fit_inside_the_pages(self):
        for view in ("alarm", "timer"):
            _screen, dialog = self.open_editor(view)
            body_width = dialog.body.get_width()
            for spin_button in self.spin_buttons(dialog):
                ok, bounds = spin_button.compute_bounds(dialog.body)
                self.assertTrue(ok)
                self.assertLessEqual(bounds.get_x() + bounds.get_width(), body_width + 1, view)
            dialog.close()
            pump(0.3)


class StopwatchTests(GtkAppTestCase):
    def labels(self, view):
        return text_of(view.left_button), text_of(view.right_button)

    def test_buttons_follow_the_strict_state_machine_and_laps_show_gaps(self):
        view = self.go("stopwatch")
        self.assertEqual(self.labels(view), ("Start", "Lap"))
        self.assertFalse(view.right_button.is_sensitive())
        self.assertEqual(view.clock_label.get_text(), "00:00:00.00")
        self.assertTrue(view.empty_label.get_mapped())

        self.click(view.left_button)
        self.assertEqual(self.labels(view), ("Pause", "Lap"))
        self.assertTrue(view.right_button.is_sensitive())
        for gap in (10.0, 11.15, 10.73):
            self.advance(gap)
            self.click(view.right_button)

        self.assertEqual(
            view.lap_table(),
            [
                ("3", "00:00:10.73", "-0.42s", "00:00:31.88"),
                ("2", "00:00:11.15", "+1.15s", "00:00:21.15"),
                ("1", "00:00:10.00", "First lap", "00:00:10.00"),
            ],
        )
        self.assertEqual(view.clock_label.get_text(), "00:00:31.88")
        self.assertFalse(view.empty_label.get_visible())

        self.click(view.left_button)
        self.assertEqual(self.labels(view), ("Resume", "Clear"))
        self.advance(100)
        self.assertEqual(view.clock_label.get_text(), "00:00:31.88")
        self.click(view.left_button)
        self.assertEqual(self.labels(view), ("Pause", "Lap"))
        self.advance(1)
        self.assertEqual(view.clock_label.get_text(), "00:00:32.88")
        self.click(view.left_button)
        self.click(view.right_button)

        self.assertEqual(self.labels(view), ("Start", "Lap"))
        self.assertEqual(view.lap_table(), [])
        self.assertEqual(view.clock_label.get_text(), "00:00:00.00")
        self.assertFalse(view.right_button.is_sensitive())

    def test_stopwatch_keeps_running_while_another_view_is_open(self):
        view = self.go("stopwatch")
        self.click(view.left_button)

        self.go("report")
        self.advance(65)
        view = self.go("stopwatch")

        self.assertEqual(view.clock_label.get_text(), "00:01:05.00")


class TimerFlowTests(GtkAppTestCase):
    def open_dialog(self):
        return self.open_editor("timer")

    def fill(self, dialog, seconds: int, name: str = "Tea") -> None:
        dialog.hours_spin.set_value(0)
        dialog.minutes_spin.set_value(seconds // 60)
        dialog.seconds_spin.set_value(seconds % 60)
        dialog.name_entry.set_text(name)

    def test_timer_with_imported_music_loops_until_the_alarm_is_stopped(self):
        timers, dialog = self.open_dialog()
        self.assertEqual(len(dialog.sound_picker.sound_names()), 6)
        self.chooser.next_open = make_wav(self.tmp / "My Loop.wav", seconds=0.3)

        self.click(dialog.sound_picker.import_button)
        pump(0.2)
        self.assertEqual(dialog.sound_picker.sound_names()[-1], "My Loop")
        self.assertTrue(dialog.sound_picker.get_sound_id().startswith("custom-"))
        self.assertEqual(self.chooser.requests[-1], ("open", "Import Custom Audio", "Audio files"))
        self.fill(dialog, 2)
        self.click(dialog.save_button)
        pump(0.3)

        timer = self.repo.list_timers()[0]
        self.assertEqual((timer.name, timer.duration_seconds), ("Tea", 2))
        self.assertTrue(timer.sound_id.startswith("custom-"))
        self.click(timers.cards[timer.id]["buttons"]["start"])
        self.advance(1)
        self.assertEqual(timers.cards[timer.id]["ring"].text, "00:01")
        self.advance(1)

        expired = self.window.alert_dialog
        self.assertIsInstance(expired, TimerExpiredDialog)
        self.assertEqual(expired.name_label.get_text(), "Tea")
        self.assertTrue(pump_until(lambda: self.app.audio.alert_loop_count >= 3, 8))
        self.assertEqual(self.app.audio.alert_state(), "playing")
        self.assertIn("Timer finished", self.notifier.titles())
        self.assertEqual(timers.cards[timer.id]["status"], "Finished")

        self.click(expired.stop_button)
        pump(0.2)

        self.assertEqual(self.app.audio.alert_state(), "null")
        self.assertFalse(self.app.audio._alert.has_pipeline)
        self.assertIsNone(self.window.alert_dialog)
        self.assertEqual(timers.cards[timer.id]["status"], "Ready")

    def test_escape_on_an_expired_timer_keeps_the_sound_and_restart_runs_it_again(self):
        timer = self.repo.create_timer(timer_draft(duration_seconds=5, sound_id="bundled-community"))
        timers = self.go("timer")
        self.click(timers.cards[timer.id]["buttons"]["start"])
        self.advance(5)
        expired = self.window.alert_dialog
        self.assertTrue(self.audio_playing())

        self.press_escape(expired)
        self.assertEqual(self.window.alert_bar_label.get_text(), "Timer finished: Tea")
        self.assertEqual(self.app.audio.alert_state(), "playing")
        self.click(timers.cards[timer.id]["buttons"]["restart"])
        pump(0.2)

        self.assertEqual(self.scheduler.timer_run(timer.id).status, "running")
        self.assertEqual(self.app.audio.alert_state(), "null")
        self.assertFalse(self.window.alert_bar.get_visible())

    def test_start_pause_resume_and_reset(self):
        timer = self.repo.create_timer(timer_draft(duration_seconds=90))
        timers = self.go("timer")
        self.assertEqual(timers.cards[timer.id]["ring"].text, "01:30")

        self.click(timers.cards[timer.id]["buttons"]["start"])
        self.advance(30)
        self.assertEqual(timers.cards[timer.id]["ring"].text, "01:00")
        self.assertTrue(self.window.live_pill.get_mapped())
        self.click(timers.cards[timer.id]["buttons"]["pause"])
        self.advance(500)
        self.assertEqual(timers.cards[timer.id]["ring"].text, "01:00")
        self.click(timers.cards[timer.id]["buttons"]["resume"])
        self.advance(10)
        self.assertEqual(timers.cards[timer.id]["ring"].text, "00:50")
        self.click(timers.cards[timer.id]["buttons"]["reset"])

        self.assertIsNone(self.scheduler.timer_run(timer.id))
        self.assertEqual(timers.cards[timer.id]["ring"].text, "01:30")
        self.assertEqual(sorted(timers.cards[timer.id]["buttons"]), ["delete", "edit", "start"])

    def test_non_audio_file_is_rejected_by_the_importer_with_a_visible_reason(self):
        _timers, dialog = self.open_dialog()
        fake = self.tmp / "notes.mp3"
        fake.write_text("this is not audio", encoding="utf-8")
        self.chooser.next_open = fake

        self.click(dialog.sound_picker.import_button)
        pump(0.2)

        self.assertTrue(dialog.sound_picker.error_label.get_mapped())
        self.assertEqual(
            dialog.sound_picker.error_label.get_text(),
            "That file's content does not match its audio extension.",
        )
        self.assertEqual(len(dialog.sound_picker.sound_names()), 6)

    def test_preview_button_plays_and_stops_the_selected_sound(self):
        _timers, dialog = self.open_dialog()
        picker = dialog.sound_picker
        picker.set_sound_id("bundled-community")

        self.assertEqual(picker.preview_button.get_tooltip_text(), "Play preview")
        self.assertEqual(picker.import_button.get_tooltip_text(), "Import your own audio file")
        self.click(picker.preview_button)
        self.assertEqual(picker.preview_button.get_icon_name(), "media-playback-stop-symbolic")
        self.assertEqual(picker.preview_button.get_tooltip_text(), "Stop preview")
        self.assertTrue(pump_until(lambda: self.app.audio.preview_state() == "playing", 5))
        self.click(picker.preview_button)

        self.assertEqual(picker.preview_button.get_icon_name(), "media-playback-start-symbolic")
        self.assertEqual(picker.preview_button.get_tooltip_text(), "Play preview")
        self.assertEqual(self.app.audio.preview_state(), "null")

    def test_a_running_timer_reopens_paused_with_its_time_left_after_quitting(self):
        timer = self.repo.create_timer(timer_draft(name="Laundry", duration_seconds=600))
        timers = self.go("timer")
        self.click(timers.cards[timer.id]["buttons"]["start"])
        self.advance(125)
        self.close_app()

        self.launch()

        timers = self.go("timer")
        run = self.scheduler.timer_run(timer.id)
        self.assertEqual((run.status, run.remaining_ms), ("paused", 475_000))
        self.assertEqual(timers.cards[timer.id]["status"], "Paused")
        self.assertTrue(timers.cards[timer.id]["buttons"]["resume"].get_mapped())

    def test_a_break_reopens_paused_with_its_time_left_after_quitting(self):
        target = self.repo.create_target(EXAMPLE_1)
        self.scheduler.start_target(target.id)
        self.scheduler.complete_subtask()
        self.advance(4 * 60 + 30)
        self.close_app()

        self.launch()

        state = self.scheduler.session.state
        self.assertEqual((state.phase, state.remaining_ms), (TargetPhase.PAUSED_REST, 330_000))
        today = self.window.view("today")
        self.assertEqual(today.hero_time.get_text(), "00:05:30")

    def test_timer_needs_a_duration_and_a_name(self):
        _timers, dialog = self.open_dialog()

        self.fill(dialog, 0, name="Zero")
        self.click(dialog.save_button)
        self.assertEqual(dialog.error_label.get_text(), "Set a duration of at least 1 second.")
        self.fill(dialog, 30, name="")
        self.click(dialog.save_button)

        self.assertEqual(dialog.error_label.get_text(), "Timer name: This field cannot be empty.")
        self.assertEqual(self.repo.list_timers(), [])

    def test_timer_can_be_edited_and_deleted(self):
        timer = self.repo.create_timer(timer_draft(duration_seconds=3725))
        timers = self.go("timer")

        self.click(timers.cards[timer.id]["buttons"]["edit"])
        pump(0.3)
        dialog = timers.editor
        self.assertEqual(dialog.title_label.get_text(), "Edit Timer")
        values = [s.get_value_as_int() for s in (dialog.hours_spin, dialog.minutes_spin, dialog.seconds_spin)]
        self.assertEqual(values, [1, 2, 5])
        self.assertEqual(dialog.seconds_spin.get_text(), "05")
        dialog.name_entry.set_text("Laundry")
        self.click(dialog.save_button)
        pump(0.3)
        self.assertEqual(timers.cards[timer.id]["name"].get_text(), "Laundry")

        self.click(timers.cards[timer.id]["buttons"]["delete"])
        self.respond(self.window.last_confirm, "confirm")
        pump(0.3)

        self.assertEqual(self.repo.list_timers(), [])
        self.assertEqual(timers.states.state, "empty")


class ReportAndBackupTests(GtkAppTestCase):
    def complete_one_subtask(self, **overrides):
        target = self.repo.create_target(short_target(**overrides))
        self.scheduler.start_target(target.id)
        self.advance(60)
        self.click(self.window.alert_dialog.complete_button)
        return target

    def test_report_shows_database_totals_chart_and_log(self):
        self.complete_one_subtask()
        report = self.go("report")

        self.assertEqual((report.task_value.get_text(), report.duration_value.get_text()), ("1", "1m"))
        self.assertEqual(report.detail.get_visible_child_name(), "data")
        self.assertEqual(report.log_table(), [("08:01", "First step", "Quick target", "1m", "Completed")])
        self.assertEqual(report.period_label.get_text(), "Mon, 16 Nov 2026")
        self.assertFalse(report.next_button.is_sensitive())

        chart = report.chart
        self.assertTrue(chart.get_focusable())
        self.assertTrue(chart._on_key(None, Gdk.KEY_Right, 0, 0))
        self.assertEqual(report.chart_focus_label.get_text(), "00:00: 0m")
        for _ in range(8):
            chart._on_key(None, Gdk.KEY_Right, 0, 0)
        self.assertEqual((chart.focus_index, report.chart_focus_label.get_text()), (8, "08:00: 1m"))
        chart._on_key(None, Gdk.KEY_Left, 0, 0)
        self.assertEqual(chart.focus_index, 7)
        chart.set_focus_index(chart.index_at(chart.get_width() - 1))
        self.assertEqual(chart.focus_index, 23)

        self.choose(report.period_group, "week")
        pump(0.3)
        self.assertEqual(report.period_label.get_text(), "16 Nov to 22 Nov 2026")
        self.assertEqual(report.chart.describe(0), "Mon: 1m")
        self.assertEqual(report.log_table()[0][0], "Mon 08:01")
        self.click(report.previous_button)
        pump(0.3)

        self.assertEqual(report.detail.get_visible_child_name(), "empty")
        self.assertEqual(report.empty_panel.title_label.get_text(), "No focus sessions in this week")
        self.assertEqual(report.task_value.get_text(), "0")
        self.click(report.next_button)
        pump(0.3)
        self.assertEqual(report.task_value.get_text(), "1")

    def test_report_exports_csv_and_json_and_neutralises_formula_cells(self):
        hostile = "=cmd|' /C calc'!A0"
        self.complete_one_subtask(subtask_titles=(hostile, "x"))
        report = self.go("report")

        self.chooser.next_save = self.tmp / "report.csv"
        self.click(report.csv_button)
        self.chooser.next_save = self.tmp / "report.json"
        self.click(report.json_button)

        self.assertEqual(
            self.chooser.requests[-2], ("save", "Export report as CSV", "productime-report-2026-11-16.csv")
        )
        with open(self.tmp / "report.csv", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[1][rows[0].index("Subtask")], "'" + hostile)
        self.assertEqual(rows[1][rows[0].index("Completed")], "yes")
        payload = json.loads((self.tmp / "report.json").read_text(encoding="utf-8"))
        self.assertEqual((payload["task_completed"], payload["logs"][0]["title"]), (1, hostile))
        self.assertEqual(self.window.last_toast, "Report exported to report.json")

    def test_backup_is_saved_and_restored_through_the_data_panel(self):
        target = self.complete_one_subtask()
        self.repo.create_alarm(alarm_draft(repeat_days=(1, 2, 3)))
        self.repo.create_timer(timer_draft())
        self.scheduler.quit_session()
        settings = self.open_settings()
        backup_path = self.tmp / "my-backup.json"

        self.chooser.next_save = backup_path
        self.activate_row(settings.save_row)
        self.assertEqual(
            self.chooser.requests[-1], ("save", "Save Backup", "productime-backup-20261116.json")
        )
        self.assertEqual(self.window.last_toast, "Backup saved to my-backup.json")
        payload = json.loads(backup_path.read_text(encoding="utf-8"))
        self.assertEqual(
            (payload["format"], len(payload["targets"]), len(payload["focus_logs"])),
            ("productime-backup", 1, 1),
        )

        self.repo.delete_target(target.id)
        self.repo.delete_alarm(self.repo.list_alarms()[0].id)
        self.repo.delete_timer(self.repo.list_timers()[0].id)
        self.chooser.next_open = backup_path
        self.activate_row(settings.restore_row)
        confirm = self.window.last_confirm
        self.assertIn("replaces every target, alarm, timer, and report entry", confirm.get_body())
        self.respond(confirm, "confirm")
        pump(0.3)

        self.assertEqual(self.window.last_toast, "Backup restored.")
        self.assertEqual(
            (len(self.repo.list_targets()), len(self.repo.list_alarms()), len(self.repo.list_timers())),
            (1, 1, 1),
        )
        self.assertIn(target.id, self.window.view("today").rows)
        report = self.go("report")
        self.assertEqual(report.task_value.get_text(), "1")

    def test_cancelling_restore_changes_nothing(self):
        self.repo.create_timer(timer_draft())
        self.chooser.next_open = self.tmp / "unused.json"

        self.activate_row(self.open_settings().restore_row)
        self.respond(self.window.last_confirm, "cancel")

        self.assertEqual(self.chooser.requests, [])
        self.assertEqual(len(self.repo.list_timers()), 1)

    def test_corrupt_backup_is_refused_and_current_data_is_kept(self):
        self.repo.create_timer(timer_draft())
        bad = self.tmp / "bad.json"
        bad.write_text(
            '{"format": "productime-backup", "version": 1, "timers": [{"id": "x"}]}', encoding="utf-8"
        )

        self.window.restore_from_path(bad)
        pump(0.2)

        error = self.window.last_error
        self.assertEqual(error.get_heading(), "Backup could not be restored")
        self.assertIn("Your current data was not changed.", error.get_body())
        self.assertEqual([t.name for t in self.repo.list_timers()], ["Tea"])
        error.force_close()

    def test_open_data_folder_points_at_the_private_xdg_data_directory(self):
        self.activate_row(self.open_settings().folder_row)

        self.assertEqual(self.chooser.opened_folders, [self.data_dir])
        database = self.data_dir / "productime.db"
        self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.data_dir.stat().st_mode), 0o700)

    def test_saved_items_are_still_there_after_a_restart(self):
        target = self.repo.create_target(EXAMPLE_1)
        self.repo.create_alarm(alarm_draft(repeat_days=(1, 2, 3, 4, 5)))
        self.repo.create_timer(timer_draft())
        self.close_app()

        self.launch()

        today = self.window.view("today")
        self.assertEqual(today.rows[target.id]["total"].get_text(), "6h 40m total")
        self.assertEqual(len(self.go("alarm").cards), 1)
        self.assertEqual(len(self.go("timer").cards), 1)


class BackgroundTests(GtkAppTestCase):
    def test_closing_with_a_running_timer_hides_the_window_and_expiry_brings_it_back(self):
        timer = self.repo.create_timer(timer_draft(duration_seconds=30, sound_id="bundled-community"))
        self.scheduler.start_timer(timer.id)

        self.window.close()
        pump(0.2)
        self.assertIsNotNone(self.app.window)
        self.assertFalse(self.window.get_visible())
        self.assertIn("Productime is still running", self.notifier.titles())
        self.clock.advance(30)
        self.scheduler.poll()
        pump(0.4)

        self.assertTrue(self.window.get_visible())
        self.assertIsInstance(self.window.alert_dialog, TimerExpiredDialog)
        self.assertTrue(self.audio_playing())

    def test_activating_the_app_again_restores_a_hidden_window(self):
        self.repo.create_alarm(alarm_draft(repeat_days=(1, 2, 3, 4, 5, 6, 7)))
        self.scheduler.refresh_alarms()
        self.window.close()
        pump(0.2)
        self.assertFalse(self.window.get_visible())

        self.app.activate()
        pump(0.3)

        self.assertTrue(self.window.get_mapped())

    def test_turning_off_background_mode_lets_close_end_the_window_while_a_timer_runs(self):
        timer = self.repo.create_timer(timer_draft(duration_seconds=600))
        self.scheduler.start_timer(timer.id)
        pin = self.window.pin_button()
        self.assertTrue(pin.get_active())

        self.click(pin)
        pump(0.1)
        self.assertEqual(self.repo.get_pref("close_to_background", "1"), "0")
        self.window.close()
        pump(0.2)

        self.assertIsNone(self.app.window)

    def test_closing_with_nothing_pending_ends_the_app_window(self):
        self.window.close()
        pump(0.2)

        self.assertIsNone(self.app.window)


class SettingsTests(GtkAppTestCase):
    def test_settings_opens_from_the_gear_and_groups_everything_in_one_place(self):
        settings = self.open_settings()

        self.assertIsInstance(settings, Adw.PreferencesDialog)
        titles = []
        child = settings.get_visible_page().get_first_child()
        stack = [child]
        while stack:
            widget = stack.pop()
            if widget is None:
                continue
            if isinstance(widget, Adw.PreferencesGroup) and widget.get_title():
                titles.append(widget.get_title())
            stack.extend([widget.get_first_child(), widget.get_next_sibling()])
        self.assertEqual(
            sorted(titles),
            sorted(["Appearance", "Sound", "Notifications", "Background", "Data and Backup", "About"]),
        )
        for row in (settings.save_row, settings.restore_row, settings.folder_row, settings.welcome_row):
            self.assertTrue(row.get_mapped())

    def test_theme_choice_in_settings_includes_following_the_system(self):
        settings = self.open_settings()

        for scheme in ("dark", "light", "system"):
            settings.theme_toggle.set_active_name(scheme)
            pump(0.1)
            self.assertEqual(self.repo.get_pref("theme", ""), scheme)
            self.assertEqual(self.app.theme.scheme, scheme)

    def test_notification_switches_silence_only_their_own_kind(self):
        settings = self.open_settings()
        settings.notification_rows["notify_timers"].set_active(False)
        pump(0.05)
        timer = self.repo.create_timer(timer_draft(duration_seconds=5))
        alarm = self.repo.create_alarm(alarm_draft())
        self.scheduler.refresh_alarms()

        self.scheduler.start_timer(timer.id)
        self.advance(300)

        self.assertEqual(self.repo.get_pref("notify_timers", ""), "0")
        keys = [key for key, _title, _body in self.notifier.sent]
        self.assertNotIn(f"timer-{timer.id}", keys)
        self.assertIn(f"alarm-{alarm.id}", keys)

    def test_start_at_login_writes_and_removes_the_autostart_entry(self):
        settings = self.open_settings()
        autostart = self.tmp / "config" / "autostart" / "org.productime.Productime.desktop"
        self.assertFalse(settings.autostart_row.get_active())

        settings.autostart_row.set_active(True)
        pump(0.05)
        self.assertTrue(autostart.is_file())
        self.assertIn("--background", autostart.read_text(encoding="utf-8"))
        settings.autostart_row.set_active(False)
        pump(0.05)

        self.assertFalse(autostart.exists())

    def test_backup_rows_explain_themselves_and_start_the_file_dialogs(self):
        settings = self.open_settings()
        self.chooser.next_save = None

        self.activate_row(settings.save_row)

        self.assertEqual(self.chooser.requests[-1][:2], ("save", "Save Backup"))

    def test_show_welcome_again_reopens_the_welcome(self):
        settings = self.open_settings()

        self.activate_row(settings.welcome_row)
        pump(0.4)

        self.assertIsNotNone(self.window.welcome_dialog)
        self.assertTrue(self.window.welcome_dialog.next_button.get_mapped())


class WelcomeTests(GtkAppTestCase):
    show_welcome = True

    def test_first_launch_shows_the_welcome_pages_and_get_started_applies_the_choices(self):
        welcome = self.window.welcome_dialog
        self.assertIsNotNone(welcome)
        self.assertEqual(len(welcome.pages), 4)
        self.assertEqual(text_of(welcome.next_button), "Next")
        self.assertFalse(welcome.back_button.get_visible())

        for _ in range(3):
            self.click(welcome.next_button)
            pump(0.1)
        self.assertEqual(welcome.page_index, 3)
        self.assertEqual(text_of(welcome.next_button), "Get Started")
        self.assertTrue(welcome.autostart_row.get_mapped())
        self.assertTrue(welcome.back_button.get_visible())
        welcome.keep_running_row.set_active(False)
        self.click(welcome.next_button)
        pump(0.4)

        self.assertTrue(welcome.finished)
        self.assertIsNone(self.window.welcome_dialog)
        self.assertEqual(self.repo.get_pref("onboarding_done", ""), "1")
        self.assertEqual(self.repo.get_pref("close_to_background", ""), "0")
        autostart = self.tmp / "config" / "autostart" / "org.productime.Productime.desktop"
        self.assertTrue(autostart.is_file())

    def test_welcome_is_shown_only_once(self):
        self.window.welcome_dialog.close()
        pump(0.4)
        self.close_app()

        self.launch()

        self.assertIsNone(self.window.welcome_dialog)

    def test_skipping_never_turns_on_start_at_login(self):
        welcome = self.window.welcome_dialog

        self.click(welcome.skip_button)
        pump(0.4)

        self.assertFalse(welcome.finished)
        self.assertEqual(self.repo.get_pref("onboarding_done", ""), "1")
        self.assertFalse((self.tmp / "config" / "autostart").exists())

    def test_escape_closes_the_welcome(self):
        welcome = self.window.welcome_dialog

        self.press_escape(welcome)

        self.assertIsNone(self.window.welcome_dialog)
        self.assertEqual(self.repo.get_pref("onboarding_done", ""), "1")


class GnomeIntegrationTests(GtkAppTestCase):
    def test_alarm_notification_buttons_stop_and_snooze_without_the_window(self):
        alarm = self.repo.create_alarm(alarm_draft(snooze_limit=None))
        self.scheduler.refresh_alarms()
        self.advance(300)
        self.assertTrue(self.audio_playing())
        self.assertEqual(self.notifier.button_labels(f"alarm-{alarm.id}"), ["Stop", "Snooze 5 min"])

        self.app.activate_action("alarm-snooze", GLib.Variant("s", alarm.id))
        pump(0.2)
        self.assertIn(alarm.id, self.scheduler.snoozed)
        self.assertEqual(self.app.audio.alert_state(), "null")
        self.advance(300)
        self.app.activate_action("alarm-stop", GLib.Variant("s", alarm.id))
        pump(0.2)

        self.assertEqual(self.scheduler.ringing, [])
        self.assertIsNone(self.window.alert_dialog)
        self.assertEqual(self.app.audio.alert_state(), "null")

    def test_a_mission_alarm_cannot_be_stopped_from_the_notification(self):
        alarm = self.repo.create_alarm(alarm_draft(mission_mode="typing"))
        self.scheduler.refresh_alarms()
        self.advance(300)

        self.app.activate_action("alarm-stop", GLib.Variant("s", alarm.id))
        pump(0.2)

        self.assertEqual(len(self.scheduler.ringing), 1)
        self.assertEqual(self.window.last_toast, "Complete the mission to stop this alarm.")
        self.assertIsInstance(self.window.alert_dialog, AlarmRingingDialog)

    def test_gate_and_timer_notification_buttons(self):
        target = self.repo.create_target(short_target())
        self.scheduler.start_target(target.id)
        self.advance(60)
        self.assertEqual(self.notifier.button_labels("target-gate"), ["Check Off", "Snooze 5 min"])

        self.app.activate_action("gate-snooze", GLib.Variant("i", 5))
        pump(0.1)
        self.assertEqual(self.scheduler.session.state.phase, TargetPhase.SNOOZING)
        self.advance(300)
        self.app.activate_action("gate-complete", None)
        pump(0.1)
        self.assertEqual(self.scheduler.session.state.phase, TargetPhase.RESTING)

        timer = self.repo.create_timer(timer_draft(duration_seconds=5))
        self.scheduler.start_timer(timer.id)
        self.advance(5)
        self.app.activate_action("timer-restart", GLib.Variant("s", timer.id))
        pump(0.1)
        self.assertEqual(self.scheduler.timer_run(timer.id).status, "running")
        self.advance(5)
        self.app.activate_action("timer-stop", GLib.Variant("s", timer.id))
        pump(0.1)
        self.assertIsNone(self.scheduler.timer_run(timer.id))

    def test_dock_quick_actions_open_the_right_dialog(self):
        for action, view in (("new-target", "today"), ("new-alarm", "alarm"), ("new-timer", "timer")):
            self.app.activate_action("open", GLib.Variant("s", action))
            pump(0.4)
            self.assertEqual(self.window.current, view)
            editor = self.window.view(view).editor
            self.assertTrue(editor.save_button.get_mapped(), action)
            self.assertIsNone(self.window.get_visible_dialog(), action)
            self.app.activate_action("open", GLib.Variant("s", action))
            pump(0.3)
            self.assertIs(self.window.view(view).editor, editor, "a second request reuses the open page")
            editor.close()
            pump(0.3)

        self.app.activate_action("open", GLib.Variant("s", "stopwatch"))
        pump(0.2)
        self.assertEqual(self.window.current, "stopwatch")
        self.assertEqual(self.scheduler.stopwatch.status.value, "running")

    def test_desktop_entry_lists_the_quick_actions(self):
        text = (_bootstrap.ROOT / "packaging" / "org.productime.Productime.desktop.in").read_text()
        keyfile = GLib.KeyFile()
        keyfile.load_from_data(text, len(text.encode()), GLib.KeyFileFlags.NONE)

        actions = keyfile.get_string_list("Desktop Entry", "Actions")
        self.assertEqual(actions, ["new-target", "new-alarm", "new-timer", "stopwatch"])
        for action in actions:
            exec_line = keyfile.get_string(f"Desktop Action {action}", "Exec")
            self.assertEqual(exec_line, f"@EXEC@ --open={action}")

    def test_activities_search_finds_and_starts_targets_and_timers(self):
        target = self.repo.create_target(EXAMPLE_1)
        timer = self.repo.create_timer(timer_draft(name="Tea break"))

        self.assertEqual(self.app.search(["web", "company"]), [f"target:{target.id}"])
        self.assertEqual(self.app.search(["TEA"]), [f"timer:{timer.id}"])
        self.assertEqual(self.app.search(["coding"]), [f"target:{target.id}"])
        self.assertEqual(self.app.search(["   "]), [])
        metas = self.app.describe_results([f"target:{target.id}", f"timer:{timer.id}", "target:gone"])
        self.assertEqual(
            metas,
            [
                (f"target:{target.id}", "Website Profile Company", "Start target, 6h 40m"),
                (f"timer:{timer.id}", "Tea break", "Start timer, 1m"),
            ],
        )

        self.app.activate_result(f"target:{target.id}")
        pump(0.3)
        self.assertEqual(self.scheduler.session.target.id, target.id)
        self.assertEqual(self.window.view("today").pages.get_visible_child_name(), "focus")
        self.app.activate_result(f"timer:{timer.id}")
        pump(0.3)
        self.assertEqual(self.window.current, "timer")
        self.assertEqual(self.scheduler.timer_run(timer.id).status, "running")


class StartHiddenTests(GtkAppTestCase):
    start_hidden = True

    def test_login_start_keeps_no_window_until_an_alarm_rings(self):
        self.assertIsNone(self.app.window)
        self.assertFalse(self.app._held)
        alarm = self.repo.create_alarm(alarm_draft())
        self.scheduler.refresh_alarms()
        self.assertTrue(self.app._held)

        self.clock.advance(300)
        self.scheduler.poll()
        pump(0.5)

        self.assertIsNotNone(self.app.window)
        self.assertTrue(self.app.window.get_mapped())
        self.assertIsInstance(self.app.window.alert_dialog, AlarmRingingDialog)
        self.assertEqual(self.app.window.alert_dialog.alarm_id, alarm.id)

    def test_nothing_pending_releases_the_process(self):
        self.repo.create_alarm(alarm_draft(repeat_days=(1,)))
        self.scheduler.refresh_alarms()
        self.assertTrue(self.app._held)

        self.repo.set_alarm_enabled(self.repo.list_alarms()[0].id, False)
        self.scheduler.refresh_alarms()

        self.assertFalse(self.app._held)


class RealProcessTests(unittest.TestCase):
    """Launches Productime exactly as the desktop launcher does and inspects the live process."""

    APP_ID = "org.productime.Productime.UiTest"

    def test_idle_process_stays_under_100_mb_and_logs_no_warnings(self):
        with tempfile.TemporaryDirectory(prefix="productime-rss-") as tmp:
            env = dict(os.environ, XDG_DATA_HOME=tmp, PYTHONPATH=str(_bootstrap.ROOT / "src"))
            env.pop("GSK_RENDERER", None)
            process = subprocess.Popen(
                [sys.executable, "-m", "productime", f"--gapplication-app-id={self.APP_ID}"],
                env=env,
                cwd=tmp,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            try:
                database = Path(tmp) / "productime" / "productime.db"
                deadline = time.monotonic() + 20
                while not database.exists() and time.monotonic() < deadline and process.poll() is None:
                    time.sleep(0.1)
                self.assertTrue(database.exists(), "the app did not create its database under XDG_DATA_HOME")

                samples = []
                for _ in range(8):
                    time.sleep(0.5)
                    self.assertIsNone(process.poll(), "the app exited on its own")
                    result = subprocess.run(
                        ["ps", "-o", "rss=", "-p", str(process.pid)],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    samples.append(int(result.stdout.strip()))
                rss_kb = samples[-1]
                # Written to the unittest stream so the measured value is part of the test record.
                sys.stderr.write(
                    f"observed idle RSS of the running Productime process: {rss_kb / 1024:.1f} MB ... "
                )
                sys.stderr.flush()

                self.assertLess(abs(samples[-1] - samples[-2]), 2048, "memory was still growing")
                self.assertLess(rss_kb, 100 * 1024)
                self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)

                subprocess.run(["gapplication", "action", self.APP_ID, "quit"], check=True, timeout=10)
                self.assertEqual(process.wait(timeout=10), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            errors = process.stderr.read().decode("utf-8", "replace")
            process.stderr.close()
        self.assertEqual([line for line in errors.splitlines() if NATIVE_PROBLEM.search(line)], [])


if __name__ == "__main__":
    unittest.main()
