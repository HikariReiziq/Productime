"""Today screen: hero card for the live or next target, and the saved target list."""

from gi.repository import Gtk

from ....domain.formatters import format_hm, format_hms
from ....domain.models import Target, TargetPhase
from ....domain.target_engine import InvalidTransition
from ....services.scheduler_service import SchedulerError
from ...widgets.controls import chip, clear_box, hbox, icon_button, make_button, text_label, vbox, wrap_row
from ...widgets.state_panels import StatePanel, ViewStates
from .create_target_dialog import CreateTargetDialog
from .focus_target_view import PHASE_TEXT, FocusTargetView

_PAUSED = (TargetPhase.PAUSED_WORK, TargetPhase.PAUSED_REST)


class TodayView(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.repo = window.app.repo
        self.scheduler = window.scheduler
        self.dialog: CreateTargetDialog | None = None
        self.rows: dict[str, dict] = {}
        self._targets: list[Target] = []

        self.pages = Gtk.Stack()
        self.pages.set_transition_type(Gtk.StackTransitionType.NONE)
        self.pages.set_vhomogeneous(False)
        self.pages.set_hhomogeneous(False)
        self.focus_view = FocusTargetView(window, on_back=self.show_list)
        self.pages.add_named(self._build_list_page(), "list")
        self.pages.add_named(self.focus_view, "focus")
        self.append(self.pages)

    def _build_list_page(self) -> Gtk.Box:
        self.date_label = text_label("", "secondary")
        self.create_button = make_button(
            "Create Target", "primary", on_click=self.create, icon="list-add-symbolic"
        )
        header = wrap_row(
            vbox(2, text_label("Today", "page-title"), self.date_label),
            self.create_button,
            spacing=12,
            spread=True,
        )

        self.hero = vbox(6)
        self.hero.add_css_class("card")
        self.hero_dot = Gtk.Box()
        self.hero_dot.add_css_class("status-dot")
        self.hero_dot.set_valign(Gtk.Align.CENTER)
        self.hero_name = text_label("", "card-title", ellipsize=True)
        self.hero_name.set_hexpand(True)
        self.hero_time = text_label("", "hero-time", "numeric")
        self.hero_detail = text_label("", "secondary", wrap=True)
        self.hero_chips = wrap_row(spacing=6)
        self.hero_button = make_button("Start Focus", "primary", on_click=self._on_hero_action)
        self.hero_button.set_halign(Gtk.Align.START)
        self.hero_button.set_margin_top(6)
        for child in (
            hbox(8, self.hero_dot, self.hero_name),
            self.hero_time,
            self.hero_detail,
            self.hero_chips,
            self.hero_button,
        ):
            self.hero.append(child)

        self.list_box = vbox(8)
        content = vbox(16, self.hero, text_label("Targets", "section-title"), self.list_box)
        empty = StatePanel(
            "No targets planned for today",
            "Create a time-blocked Pomodoro target to break your work into focused batches.",
            action_label="Create Target",
            on_action=self.create,
            icon="productime-today-symbolic",
        )
        self.states = ViewStates(content, noun="targets", empty=empty, on_retry=self.reload)
        return vbox(20, header, self.states)

    def on_shown(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.date_label.set_text(self.window.app.clock.now().strftime("%A, %d %B %Y"))
        self.states.load(self.repo.list_targets, self._render)

    def on_scheduler_changed(self) -> None:
        self.states.run_now(self.repo.list_targets, self._render)
        self.focus_view.refresh()
        if self.scheduler.session is None and self.pages.get_visible_child_name() == "focus":
            self.show_list()

    def tick(self) -> None:
        if self.pages.get_visible_child_name() == "focus":
            self.focus_view.tick()
        else:
            self._update_hero()

    def set_compact(self, compact: bool) -> None:
        self.focus_view.set_compact(compact)

    def create(self) -> None:
        self.dialog = CreateTargetDialog(self.window, on_saved=lambda _t: self.reload())
        self.dialog.present(self.window)

    def _render(self, targets: list[Target]) -> None:
        self._targets = targets
        clear_box(self.list_box)
        self.rows = {}
        if not targets:
            self.states.show_empty()
            return
        for target in targets:
            self.list_box.append(self._build_row(target))
        self._update_hero()
        self.states.show_content()

    def _hero_target(self) -> Target | None:
        pending = [t for t in self._targets if t.status != "completed"]
        return (pending or self._targets or [None])[0]

    def _update_hero(self) -> None:
        session = self.scheduler.session
        clear_box(self.hero_chips)
        if session is not None:
            state, target = session.state, session.target
            self.hero_name.set_text(target.name)
            self.hero_time.set_text(format_hms(state.remaining_ms / 1000))
            self.hero_detail.set_text(session.batch.subtask_title)
            self.hero_chips.append(chip(target.tag_label, target.tag_label.lower()))
            self.hero_chips.append(chip(f"Batch {state.batch_index} of {state.batch_count}"))
            self.hero_chips.append(chip(PHASE_TEXT[state.phase]))
            self.hero_button.set_label("Resume Focus" if state.phase in _PAUSED else "Open Focus")
            self.hero_dot.set_visible(True)
            return
        target = self._hero_target()
        if target is None:
            return
        self.hero_name.set_text(target.name)
        self.hero_time.set_text(format_hms(target.total_timeline_minutes * 60))
        self.hero_detail.set_text("Projected total, including breaks and long rests.")
        self.hero_chips.append(chip(target.tag_label, target.tag_label.lower()))
        self.hero_chips.append(chip(f"{len(target.batches)} batches"))
        self.hero_button.set_label("Start Again" if target.status == "completed" else "Start Focus")
        # The dot marks a live session, so it is hidden while nothing is running.
        self.hero_dot.set_visible(False)

    def _status_chip(self, target: Target) -> Gtk.Label:
        if target.status == "completed":
            return chip("Completed", "success")
        if target.status == "in_progress":
            return chip(f"In progress: {target.completed_batches} of {len(target.batches)} done", "coding")
        return chip("Planned")

    def _build_row(self, target: Target) -> Gtk.Box:
        well = text_label(target.tag_label[:1], "icon-well", target.tag_label.lower(), xalign=0.5)
        well.set_valign(Gtk.Align.CENTER)
        name = text_label(target.name, "card-title", ellipsize=True)
        total = text_label(f"{format_hm(target.total_timeline_minutes)} total", "secondary", "numeric")
        chips = wrap_row(
            chip(target.tag_label, target.tag_label.lower()),
            chip(f"{len(target.batches)} batches"),
            self._status_chip(target),
            spacing=6,
        )
        middle = vbox(4, name, total, chips)
        middle.set_hexpand(True)

        live = self.scheduler.session is not None and self.scheduler.session.target.id == target.id
        play = icon_button(
            "media-playback-start-symbolic",
            "Open focus" if live else "Start target",
            on_click=lambda: self.start_target(target.id),
        )
        edit = icon_button("document-edit-symbolic", "Edit target", on_click=lambda: self.edit_target(target))
        edit.set_sensitive(not live)
        delete = icon_button("user-trash-symbolic", "Delete target", on_click=lambda: self.ask_delete(target))
        actions = hbox(0, play, edit, delete)
        actions.set_valign(Gtk.Align.CENTER)

        row = hbox(12, well, middle, actions)
        row.add_css_class("row-card")
        self.rows[target.id] = {
            "row": row,
            "name": name,
            "total": total,
            "play": play,
            "edit": edit,
            "delete": delete,
        }
        return row

    def _on_hero_action(self) -> None:
        if self.scheduler.session is not None:
            self.start_target(self.scheduler.session.target.id)
            return
        target = self._hero_target()
        if target is not None:
            self.start_target(target.id)

    def start_target(self, target_id: str) -> None:
        try:
            self.scheduler.start_target(target_id)
        except (SchedulerError, InvalidTransition) as error:
            self.window.toast(str(error))
            return
        self.show_focus()

    def edit_target(self, target: Target) -> None:
        self.dialog = CreateTargetDialog(self.window, target=target, on_saved=lambda _t: self.reload())
        self.dialog.present(self.window)

    def ask_delete(self, target: Target) -> None:
        self.window.confirm(
            "Delete this target?",
            "The target and its subtasks are removed. Completed work already in your report stays.",
            "Delete Target",
            lambda: self.delete_target(target.id),
        )

    def delete_target(self, target_id: str) -> None:
        session = self.scheduler.session
        if session is not None and session.target.id == target_id:
            self.scheduler.quit_session()
        self.repo.delete_target(target_id)
        self.reload()

    def show_focus(self) -> None:
        self.focus_view.refresh()
        self.pages.set_visible_child_name("focus")
        self.window.update_tick()

    def show_list(self) -> None:
        self.pages.set_visible_child_name("list")
        self.states.run_now(self.repo.list_targets, self._render)
