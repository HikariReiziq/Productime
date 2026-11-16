"""Saved timers: countdown ring per timer with start, pause, resume, reset, edit, and delete."""

from gi.repository import Gtk, Pango

from ....domain.formatters import format_countdown, format_duration
from ....domain.models import TimerPreset
from ....services.scheduler_service import SchedulerError
from ...widgets.controls import chip, clear_box, hbox, icon_button, make_button, text_label, vbox, wrap_row
from ...widgets.progress_ring import ProgressRing
from ...widgets.state_panels import StatePanel, ViewStates
from .timer_editor import TimerEditor

STATUS_CHIPS = {
    "idle": ("Ready", ""),
    "running": ("Running", "coding"),
    "paused": ("Paused", ""),
    "expired": ("Finished", "alert"),
}
RING_TONES = {"idle": "idle", "running": "focus", "paused": "idle", "expired": "alert"}


class TimerView(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.window = window
        self.repo = window.app.repo
        self.scheduler = window.scheduler
        self.cards: dict[str, dict] = {}

        self.create_button = make_button(
            "Add Timer", "primary", on_click=self.create, icon="list-add-symbolic"
        )
        header = wrap_row(text_label("Timer", "page-title"), self.create_button, spacing=12, spread=True)
        self.list_box = vbox(10)
        empty = StatePanel(
            "No timers saved",
            "Add a timer with its own sound. When it runs out the sound loops until you stop it.",
            action_label="Add Timer",
            on_action=self.create,
            icon="productime-timer-symbolic",
        )
        self.states = ViewStates(self.list_box, noun="timers", empty=empty, on_retry=self.reload)
        self.append(header)
        self.append(self.states)

    def on_shown(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.states.load(self.repo.list_timers, self._render)

    def on_scheduler_changed(self) -> None:
        self.states.run_now(self.repo.list_timers, self._render)

    def set_compact(self, compact: bool) -> None:
        pass

    def tick(self) -> None:
        for timer_id, card in self.cards.items():
            run = self.scheduler.timer_run(timer_id)
            if run is not None and run.status == "running":
                self._paint_ring(card["ring"], card["preset"], run)

    @property
    def editor(self) -> TimerEditor | None:
        return self.window.editor_for("timer")

    def create(self) -> None:
        if self.editor is not None:
            self.window.navigate("timer")
            return
        editor = TimerEditor(self.window, on_saved=lambda _t: self.reload())
        self.window.open_editor(editor, self.create_button)

    def _render(self, timers: list[TimerPreset]) -> None:
        clear_box(self.list_box)
        self.cards = {}
        if not timers:
            self.states.show_empty()
            return
        sound_names = {sound.id: sound.display_name for sound in self.repo.list_sounds()}
        for preset in timers:
            self.list_box.append(self._build_card(preset, sound_names.get(preset.sound_id, "Default sound")))
        self.states.show_content()

    @staticmethod
    def _paint_ring(ring: ProgressRing, preset: TimerPreset, run) -> None:
        if run is None:
            ring.set_progress(1.0, format_countdown(preset.duration_seconds), "", "idle")
            return
        fraction = run.remaining_ms / run.duration_ms if run.duration_ms else 0.0
        ring.set_progress(fraction, format_countdown(run.remaining_ms / 1000), "", RING_TONES[run.status])

    def _build_card(self, preset: TimerPreset, sound_name: str) -> Gtk.Box:
        run = self.scheduler.timer_run(preset.id)
        status = run.status if run else "idle"
        ring = ProgressRing(self.window.app.theme, size=96, stroke=7, text_px=17)
        self._paint_ring(ring, preset, run)

        name = text_label(preset.name, "card-title", ellipsize=True)
        length = text_label(f"{format_duration(preset.duration_seconds)} timer", "secondary", "numeric")
        sound_chip = chip(sound_name)
        sound_chip.set_max_width_chars(24)
        sound_chip.set_ellipsize(Pango.EllipsizeMode.END)
        status_text, status_kind = STATUS_CHIPS[status]
        info = vbox(4, name, length, wrap_row(sound_chip, chip(status_text, status_kind), spacing=6))
        info.set_hexpand(True)
        info.set_valign(Gtk.Align.CENTER)

        timer_id = preset.id
        buttons: dict[str, Gtk.Button] = {}
        if status == "idle":
            buttons["start"] = make_button(
                "Start", "primary", on_click=lambda: self._run("start_timer", timer_id)
            )
            buttons["edit"] = icon_button(
                "document-edit-symbolic", "Edit timer", on_click=lambda: self.edit(preset)
            )
            buttons["delete"] = icon_button(
                "user-trash-symbolic", "Delete timer", on_click=lambda: self.ask_delete(preset)
            )
        elif status == "running":
            buttons["pause"] = make_button(
                "Pause", "secondary", on_click=lambda: self._run("pause_timer", timer_id)
            )
            buttons["reset"] = make_button(
                "Reset", "ghost", on_click=lambda: self._run("reset_timer", timer_id)
            )
        elif status == "paused":
            buttons["resume"] = make_button(
                "Resume", "primary", on_click=lambda: self._run("resume_timer", timer_id)
            )
            buttons["reset"] = make_button(
                "Reset", "ghost", on_click=lambda: self._run("reset_timer", timer_id)
            )
        else:
            buttons["stop"] = make_button(
                "Stop Alarm", "primary", on_click=lambda: self._run("dismiss_timer", timer_id)
            )
            buttons["restart"] = make_button(
                "Restart Timer", "secondary", on_click=lambda: self._run("restart_timer", timer_id)
            )

        card = vbox(10, hbox(14, ring, info), wrap_row(*buttons.values(), spacing=8))
        card.add_css_class("row-card")
        self.cards[preset.id] = {
            "card": card,
            "ring": ring,
            "name": name,
            "preset": preset,
            "status": status_text,
            "buttons": buttons,
        }
        return card

    def _run(self, method: str, timer_id: str) -> None:
        try:
            getattr(self.scheduler, method)(timer_id)
        except SchedulerError as error:
            self.window.toast(str(error))

    def edit(self, preset: TimerPreset) -> None:
        editor = TimerEditor(self.window, timer=preset, on_saved=lambda _t: self.reload())
        self.window.open_editor(editor, self.cards.get(preset.id, {}).get("buttons", {}).get("edit"))

    def ask_delete(self, preset: TimerPreset) -> None:
        self.window.confirm(
            "Delete this timer?",
            "The saved timer is removed. Time already recorded in your report stays.",
            "Delete Timer",
            lambda: self.delete(preset.id),
        )

    def delete(self, timer_id: str) -> None:
        self.scheduler.forget_timer(timer_id)
        self.repo.delete_timer(timer_id)
        self.reload()
