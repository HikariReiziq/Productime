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

import _bootstrap

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk

from gtk_harness import NATIVE_PROBLEM, GtkAppTestCase, pump, pump_until, target_size, text_of
from productime.domain.models import TargetPhase
from productime.domain.target_engine import InvalidTransition
from productime.paths import styles_dir
from productime.ui.tokens import hex_to_rgb, load_tokens
from productime.ui.views.alarm.alarm_ringing_dialog import LIMIT_REACHED as ALARM_LIMIT_REACHED
from productime.ui.views.alarm.alarm_ringing_dialog import PASTE_BLOCKED, AlarmRingingDialog
from productime.ui.views.target.subtask_gate_dialog import LIMIT_REACHED, SubtaskGateDialog
from productime.ui.views.timer.timer_expired_dialog import TimerExpiredDialog
from support import EXAMPLE_1, alarm_draft, make_wav, short_target, timer_draft

VIEWS = ("today", "alarm", "stopwatch", "timer", "report")


def buttons_under(widget):
    """Every button below a widget, skipping the window manager's own controls."""
    found, stack = [], [widget]
    while stack:
        current = stack.pop()
        if isinstance(current, Gtk.WindowControls):
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
        self.assertFalse(self.window.mini_session.get_visible())
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

    def test_theme_switch_restyles_gtk_and_cairo_and_saves_the_choice(self):
        switch = self.window.settings_controls()["theme"]
        tokens = {name: load_tokens(styles_dir() / f"{name}.css") for name in ("light", "dark")}
        self.assertEqual(switch.get_active(), self.app.theme.dark)

        for _ in range(2):
            wanted_dark = not self.app.theme.dark
            switch.set_active(wanted_dark)
            pump(0.2)
            name = "dark" if wanted_dark else "light"

            self.assertEqual(self.app.theme.dark, wanted_dark)
            self.assertEqual(Adw.StyleManager.get_default().get_dark(), wanted_dark)
            self.assertEqual(self.repo.get_pref("theme", ""), name)
            self.assertEqual(self.app.theme.rgb("bg_canvas"), hex_to_rgb(tokens[name]["bg_canvas"]))
            self.assertEqual(self.app.theme.rgb("text_primary"), hex_to_rgb(tokens[name]["text_primary"]))
            for view_name in VIEWS:
                self.assertTrue(self.go(view_name).get_mapped())

    def test_theme_choice_survives_a_restart(self):
        for wanted_dark in (True, False):
            self.app.theme.set_dark(wanted_dark)
            self.close_app()

            self.launch()

            self.assertEqual(self.app.theme.dark, wanted_dark)
            self.assertEqual(self.window.settings_controls()["theme"].get_active(), wanted_dark)

    def test_volume_and_mute_controls_drive_the_audio_service_and_are_saved(self):
        controls = self.window.settings_controls()

        controls["scale"].set_value(35)
        pump(0.05)
        self.assertEqual((self.app.audio.volume, self.repo.get_pref("volume", "")), (35, "35"))
        self.assertAlmostEqual(self.app.audio.effective_volume, 0.35)
        controls["mute"].set_active(True)
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

        dialog = self.window.view("timer").dialog
        self.assertTrue(dialog.save_button.get_mapped())
        self.press_escape(dialog)
        self.assertFalse(dialog.save_button.get_mapped())

    def test_tab_moves_focus_through_the_shell_controls(self):
        controls = self.window.settings_controls()
        for name in VIEWS:
            self.assertTrue(self.window.nav_button(name).get_focusable(), name)
        for key in ("theme", "background", "mute", "scale", "save", "restore", "folder"):
            self.assertTrue(controls[key].get_focusable(), key)

        first = self.window.nav_button("today")
        first.grab_focus()
        pump(0.05)
        self.assertIs(self.window.get_focus(), first)
        seen = []
        for _ in range(4):
            self.window.child_focus(Gtk.DirectionType.TAB_FORWARD)
            pump(0.02)
            seen.append(self.window.get_focus())

        self.assertEqual(seen[:4], [self.window.nav_button(name) for name in VIEWS[1:]])

    def test_rail_stays_232_pixels_wide_and_page_actions_sit_at_the_right_edge(self):
        today = self.window.view("today")

        self.assertEqual(target_size(self.window.rail)[0], 232)
        ok, bounds = today.create_button.compute_bounds(today)
        self.assertTrue(ok)
        self.assertAlmostEqual(bounds.get_x() + bounds.get_width(), today.get_width(), delta=1)

    def test_theme_switch_follows_a_theme_change_made_elsewhere(self):
        switch = self.window.settings_controls()["theme"]

        for wanted_dark in (True, False, True):
            self.app.theme.set_dark(wanted_dark)
            pump(0.1)
            self.assertEqual(switch.get_active(), wanted_dark)

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

        self.click(self.window.settings_button)
        pump(0.3)

        dialog = self.window.settings_dialog
        self.assertTrue(self.window.settings_controls(1)["save"].get_mapped())
        self.press_escape(dialog)
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

    def test_dialogs_fit_a_narrow_window(self):
        today = self.window.view("today")
        self.click(today.create_button)
        pump(0.4)
        dialog = today.dialog

        self.assertEqual(dialog.columns.get_orientation(), Gtk.Orientation.VERTICAL)
        self.assertLessEqual(dialog.get_width(), 420)
        self.assertLessEqual(self.minimum_width(), 420)
        self.assertEqual(self.window.get_width(), 420)


class TargetFlowTests(GtkAppTestCase):
    def open_create_dialog(self):
        today = self.window.view("today")
        self.click(today.create_button)
        pump(0.3)
        self.assertTrue(today.dialog.save_button.get_mapped())
        return today, today.dialog

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
        dialog.long_switch.set_active(True)
        pump(0.1)
        self.choose(dialog.every_group, 3)
        self.choose(dialog.long_group, 30)
        self.choose(dialog.snooze_group, 3)

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
        dialog.long_switch.set_active(True)
        pump(0.1)
        self.choose(dialog.every_group, "custom")
        dialog.every_spin.set_value(4)
        self.choose(dialog.long_group, 30)
        self.choose(dialog.snooze_group, None)
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

        self.choose(dialog.pomodoro_group, "custom")
        dialog.work_spin.set_value(45)
        dialog.break_spin.set_value(15)
        pump(0.1)

        self.assertEqual(dialog.timeline.total_text, "Total Timeline: 1h 55m")
        self.assertEqual(
            [entry[2] for entry in dialog.timeline.entries()],
            ["00:00 -> 00:45", "00:45 -> 01:00", "01:00 -> 01:40", "01:40 -> 01:55"],
        )

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

    def test_escape_closes_the_create_dialog_without_saving(self):
        today, dialog = self.open_create_dialog()
        dialog.name_entry.set_text("Never saved")

        self.press_escape(dialog)

        self.assertFalse(dialog.save_button.get_mapped())
        self.assertEqual(self.repo.list_targets(), [])
        self.assertEqual(today.states.state, "empty")

    def test_gate_blocks_the_break_until_the_subtask_is_checked_off(self):
        today, _target = self.start_short_target(snooze_limit=1)
        focus = today.focus_view
        self.assertEqual(today.pages.get_visible_child_name(), "focus")
        self.assertEqual((focus.ring.text, focus.ring.tone), ("01:00", "focus"))
        self.assertTrue(self.window.mini_session.get_mapped())

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
        self.assertEqual(gate.prompt_label.get_text(), 'Batch 1 complete. Did you finish "First step"?')
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
        self.assertFalse(focus.complete_button.is_sensitive())

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
        self.assertEqual((self.phase, self.scheduler.session.state.batch_index), (TargetPhase.WORKING, 2))
        self.advance(60)
        gate = self.window.alert_dialog
        self.assertEqual(text_of(gate.complete_button), "Check Subtask & Enter Final Rest")
        self.click(gate.complete_button)

        self.assertTrue(self.scheduler.session.state.in_final_rest)
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
        self.assertFalse(self.window.mini_session.get_visible())

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
        self.assertEqual(self.window.mini_title.get_text(), name)
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
        dialog = today.dialog
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


class AlarmFlowTests(GtkAppTestCase):
    def open_dialog(self):
        alarms = self.go("alarm")
        self.click(alarms.create_button)
        pump(0.3)
        self.assertTrue(alarms.dialog.save_button.get_mapped())
        return alarms, alarms.dialog

    def ring(self, **overrides):
        alarm = self.repo.create_alarm(alarm_draft(**overrides))
        self.scheduler.refresh_alarms()
        self.advance(300)
        dialog = self.window.alert_dialog
        self.assertIsInstance(dialog, AlarmRingingDialog)
        self.assertTrue(dialog.snooze_button.get_mapped())
        return alarm, dialog

    def test_new_alarm_dialog_saves_time_repeat_sound_snooze_and_mission(self):
        alarms, dialog = self.open_dialog()
        self.assertEqual(len(dialog.sound_picker.sound_names()), 6)
        self.assertEqual(sorted(dialog.day_buttons), [1, 2, 3, 4, 5, 6, 7])

        dialog.hour_spin.set_value(8)
        dialog.minute_spin.set_value(5)
        self.choose(dialog.period_group, "AM")
        self.click(dialog.preset_buttons["Weekdays"])
        self.assertEqual(dialog.selected_days(), (1, 2, 3, 4, 5))
        self.assertEqual(dialog.repeat_summary.get_text(), "Weekdays")
        dialog.name_entry.set_text("Morning class")
        dialog.sound_picker.set_sound_id("bundled-funny")
        self.choose(dialog.interval_group, 10)
        self.choose(dialog.limit_group, 2)
        self.choose(dialog.mission_group, "math")
        self.choose(dialog.count_group, 3)
        self.choose(dialog.difficulty_group, "hard")
        self.assertIn("Hard", dialog.mission_info.get_text())
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

    def test_typing_mission_option_explains_the_ten_phrase_pool(self):
        _alarms, dialog = self.open_dialog()
        self.assertFalse(dialog.count_field.get_visible())

        self.choose(dialog.mission_group, "typing")
        self.choose(dialog.count_group, 2)

        self.assertTrue(dialog.count_field.get_mapped())
        self.assertFalse(dialog.difficulty_field.get_visible())
        self.assertIn("draws 10 phrases", dialog.mission_info.get_text())
        self.assertIn("type 2 phrases exactly", dialog.mission_info.get_text())

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
        dialog = alarms.dialog
        self.assertEqual(dialog.name_entry.get_text(), "Morning class")
        self.assertEqual(dialog.selected_days(), (6, 7))
        self.assertEqual((dialog.hour_spin.get_value_as_int(), dialog.period_group.get_value()), (8, "AM"))
        dialog.name_entry.set_text("Weekend run")
        self.click(dialog.preset_buttons["Every day"])
        self.click(dialog.save_button)
        pump(0.3)
        self.assertEqual(self.repo.get_alarm(alarm.id).name, "Weekend run")
        self.assertEqual(alarms.cards[alarm.id]["repeat"].get_text(), "Every day")

        self.click(alarms.cards[alarm.id]["delete"])
        self.respond(self.window.last_confirm, "confirm")
        pump(0.3)

        self.assertEqual(self.repo.list_alarms(), [])
        self.assertEqual(alarms.states.state, "empty")


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
        timers = self.go("timer")
        self.click(timers.create_button)
        pump(0.3)
        self.assertTrue(timers.dialog.save_button.get_mapped())
        return timers, timers.dialog

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
        self.assertTrue(self.window.mini_session.get_mapped())
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

        self.click(picker.preview_button)
        self.assertEqual(text_of(picker.preview_button), "Stop")
        self.assertTrue(pump_until(lambda: self.app.audio.preview_state() == "playing", 5))
        self.click(picker.preview_button)

        self.assertEqual(text_of(picker.preview_button), "Play")
        self.assertEqual(self.app.audio.preview_state(), "null")

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
        dialog = timers.dialog
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
        controls = self.window.settings_controls()
        backup_path = self.tmp / "my-backup.json"

        self.chooser.next_save = backup_path
        self.click(controls["save"])
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
        self.click(controls["restore"])
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

        self.click(self.window.settings_controls()["restore"])
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
        self.click(self.window.settings_controls()["folder"])

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
        switch = self.window.settings_controls()["background"]
        self.assertTrue(switch.get_active())

        switch.set_active(False)
        pump(0.1)
        self.assertEqual(self.repo.get_pref("close_to_background", "1"), "0")
        self.window.close()
        pump(0.2)

        self.assertIsNone(self.app.window)

    def test_closing_with_nothing_pending_ends_the_app_window(self):
        self.window.close()
        pump(0.2)

        self.assertIsNone(self.app.window)


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
