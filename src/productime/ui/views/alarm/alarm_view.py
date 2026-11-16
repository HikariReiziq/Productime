"""Alarm list: time, repeat days, sound, mission, next ring, and an enable switch per alarm."""

from gi.repository import GLib, Gtk, Pango

from ....domain.alarm_schedule import WEEKDAY_SHORT, describe_repeat, format_relative
from ....domain.formatters import format_alarm_time
from ....domain.models import Alarm
from ...widgets.controls import (
    chip,
    clear_box,
    hbox,
    icon_button,
    make_button,
    set_accessible_label,
    text_label,
    vbox,
    wrap_row,
)
from ...widgets.state_panels import StatePanel, ViewStates
from .create_alarm_dialog import CreateAlarmDialog


def mission_text(alarm: Alarm) -> str | None:
    if alarm.mission_mode == "typing":
        return f"Typing {alarm.mission_count}x"
    if alarm.mission_mode == "math":
        return f"Math {alarm.mission_difficulty.capitalize()} {alarm.mission_count}x"
    return None


class AlarmView(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.window = window
        self.repo = window.app.repo
        self.scheduler = window.scheduler
        self.dialog: CreateAlarmDialog | None = None
        self.cards: dict[str, dict] = {}

        self.summary_label = text_label("", "secondary", "numeric", wrap=True)
        self.create_button = make_button(
            "New Alarm", "primary", on_click=self.create, icon="list-add-symbolic"
        )
        header = wrap_row(
            vbox(2, text_label("Alarm", "page-title"), self.summary_label),
            self.create_button,
            spacing=12,
            spread=True,
        )
        self.list_box = vbox(10)
        content = vbox(12, text_label("Upcoming Alarms", "section-title"), self.list_box)
        empty = StatePanel(
            "No alarms yet",
            "Add an alarm with repeat days, a looping sound, and an optional wake-up mission.",
            action_label="New Alarm",
            on_action=self.create,
            icon="productime-alarm-symbolic",
        )
        self.states = ViewStates(content, noun="alarms", empty=empty, on_retry=self.reload)
        self.append(header)
        self.append(self.states)

    def on_shown(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.states.load(self.repo.list_alarms, self._render)

    def on_scheduler_changed(self) -> None:
        self.states.run_now(self.repo.list_alarms, self._render)

    def tick(self) -> None:
        pass

    def set_compact(self, compact: bool) -> None:
        pass

    def create(self) -> None:
        self.dialog = CreateAlarmDialog(self.window)
        self.dialog.present(self.window)

    def _render(self, alarms: list[Alarm]) -> None:
        clear_box(self.list_box)
        self.cards = {}
        upcoming = self.scheduler.next_alarm()
        if upcoming:
            alarm, when = upcoming
            relative = format_relative(when - self.window.app.clock.now())
            self.summary_label.set_text(f"Next alarm: {alarm.name}, {relative}")
        else:
            self.summary_label.set_text("No active alarms.")
        if not alarms:
            self.states.show_empty()
            return
        sound_names = {sound.id: sound.display_name for sound in self.repo.list_sounds()}
        for alarm in alarms:
            self.list_box.append(self._build_card(alarm, sound_names.get(alarm.sound_id, "Default sound")))
        self.states.show_content()

    def _build_card(self, alarm: Alarm, sound_name: str) -> Gtk.Box:
        locked = self.scheduler.is_alarm_locked(alarm.id)
        ringing = any(entry.alarm.id == alarm.id for entry in self.scheduler.ringing)

        time_label = text_label(format_alarm_time(alarm.hour_24, alarm.minute), "alarm-time", "numeric")
        time_label.set_hexpand(True)
        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_active(alarm.enabled)
        switch.set_sensitive(not locked)
        set_accessible_label(switch, f"Enable alarm at {format_alarm_time(alarm.hour_24, alarm.minute)}")
        switch.connect("notify::active", self._on_switch, alarm.id)

        name = text_label(alarm.name, "card-title", ellipsize=True)
        pills = wrap_row(spacing=4)
        for day, short in enumerate(WEEKDAY_SHORT, start=1):
            pill = text_label(short, "day-pill", xalign=0.5)
            if day in alarm.repeat_days:
                pill.add_css_class("active")
            pills.append(pill)
        # Active days are also written out, so the schedule never depends on color alone.
        repeat = text_label(describe_repeat(alarm.repeat_days), "secondary", "small")

        sound_chip = chip(sound_name)
        sound_chip.set_max_width_chars(24)
        sound_chip.set_ellipsize(Pango.EllipsizeMode.END)
        badges = wrap_row(sound_chip, spacing=6)
        mission = mission_text(alarm)
        if mission:
            badges.append(chip(mission, "study"))
        if ringing:
            badges.append(chip("Ringing", "alert"))
        elif locked:
            badges.append(chip("Snoozed", "rest"))

        if alarm.enabled:
            when = self.scheduler.next_ring_for(alarm)
            next_text = f"Rings {format_relative(when - self.window.app.clock.now())}"
        else:
            next_text = "Off"
        next_label = text_label(next_text, "secondary", "small", "numeric")
        next_label.set_hexpand(True)
        edit = icon_button("document-edit-symbolic", "Edit alarm", on_click=lambda: self.edit(alarm))
        delete = icon_button("user-trash-symbolic", "Delete alarm", on_click=lambda: self.ask_delete(alarm))
        edit.set_sensitive(not locked)
        delete.set_sensitive(not locked)

        card = vbox(
            6, hbox(12, time_label, switch), name, pills, repeat, badges, hbox(4, next_label, edit, delete)
        )
        card.add_css_class("row-card")
        self.cards[alarm.id] = {
            "card": card,
            "time": time_label,
            "name": name,
            "switch": switch,
            "repeat": repeat,
            "next": next_label,
            "edit": edit,
            "delete": delete,
            "badges": [child.get_text() for child in self._children(badges)],
        }
        return card

    @staticmethod
    def _children(box) -> list:
        found, child = [], box.get_first_child()
        while child is not None:
            found.append(child)
            child = child.get_next_sibling()
        return found

    def _on_switch(self, switch: Gtk.Switch, _param, alarm_id: str) -> None:
        # Deferred: saving re-renders the list, which would destroy the switch mid-signal.
        GLib.idle_add(self.set_enabled, alarm_id, switch.get_active())

    def set_enabled(self, alarm_id: str, enabled: bool) -> bool:
        if not self.scheduler.is_alarm_locked(alarm_id):
            self.repo.set_alarm_enabled(alarm_id, enabled)
            self.scheduler.refresh_alarms()
        return False

    def edit(self, alarm: Alarm) -> None:
        self.dialog = CreateAlarmDialog(self.window, alarm=alarm)
        self.dialog.present(self.window)

    def ask_delete(self, alarm: Alarm) -> None:
        self.window.confirm(
            "Delete this alarm?",
            "The alarm stops ringing and is removed from the list.",
            "Delete Alarm",
            lambda: self.delete(alarm.id),
        )

    def delete(self, alarm_id: str) -> None:
        if self.scheduler.is_alarm_locked(alarm_id):
            self.window.toast("Stop or finish the ringing alarm before deleting it.")
            return
        self.repo.delete_alarm(alarm_id)
        self.scheduler.refresh_alarms()
