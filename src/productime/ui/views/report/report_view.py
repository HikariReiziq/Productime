"""My Productivity: completed subtasks and focus time per day or week, from the local database."""

from datetime import date, timedelta
from pathlib import Path

from gi.repository import Gtk

from ....domain.formatters import format_duration
from ....domain.models import FocusLog, ReportSummary
from ....services import backup_service
from ....services.backup_service import BackupError
from ...widgets.controls import (
    ChoiceGroup,
    chip,
    clear_box,
    hbox,
    icon_button,
    make_button,
    text_label,
    vbox,
    wrap_row,
)
from ...widgets.state_panels import StatePanel, ViewStates
from .productivity_chart import ProductivityChart

EMPTY_TEXT = {
    "day": "No focus sessions on this day",
    "week": "No focus sessions in this week",
}


class ReportView(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.window = window
        self.repo = window.app.repo
        self.period = "day"
        self.anchor: date = window.app.clock.now().date()
        self.report: ReportSummary | None = None

        self.csv_button = make_button("Export CSV", "secondary", on_click=lambda: self.export("csv"))
        self.json_button = make_button("Export JSON", "secondary", on_click=lambda: self.export("json"))
        header = wrap_row(
            text_label("My Productivity", "page-title"),
            hbox(8, self.csv_button, self.json_button),
            spacing=12,
            spread=True,
        )

        self.previous_button = icon_button(
            "go-previous-symbolic", "Previous period", on_click=lambda: self.step(-1)
        )
        self.next_button = icon_button("go-next-symbolic", "Next period", on_click=lambda: self.step(1))
        self.period_label = text_label("", "body-strong", "numeric", xalign=0.5)
        self.period_label.set_hexpand(True)
        self.period_group = ChoiceGroup(
            [("day", "Day"), ("week", "Week")],
            "day",
            on_change=self.set_period,
            segmented=True,
            label="Report period",
        )
        navigation = wrap_row(
            hbox(4, self.previous_button, self.period_label, self.next_button),
            self.period_group,
            spacing=12,
            spread=True,
        )

        self.task_value = text_label("0", "kpi-value", "numeric")
        self.duration_value = text_label("0m", "kpi-value", "numeric")
        kpis = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, homogeneous=True)
        self.task_badge = self._badge("object-select-symbolic", "success")
        self.duration_badge = self._badge("productime-stopwatch-symbolic", "duration")
        kpis.append(self._kpi_card("Task Completed", self.task_value, self.task_badge))
        kpis.append(self._kpi_card("Time Duration", self.duration_value, self.duration_badge))

        self.chart_title = text_label("", "section-title")
        self.chart_focus_label = text_label("", "secondary", "numeric", wrap=True)
        self.chart = ProductivityChart(window.app.theme, on_focus=self._on_chart_focus)
        chart_card = vbox(8, self.chart_title, self.chart, self.chart_focus_label)
        chart_card.add_css_class("card")

        self.log_rows = vbox(0)
        log_header = hbox(12, text_label("Completed Subtasks Log", "section-title"))
        log_card = vbox(8, log_header, self.log_rows)
        log_card.add_css_class("card")

        self.data_box = vbox(18, chart_card, log_card)
        self.empty_panel = StatePanel(
            EMPTY_TEXT["day"],
            "Subtasks you check off and timers you finish are recorded here automatically.",
            action_label="Plan a Target",
            on_action=lambda: window.navigate("today"),
            icon="productime-report-symbolic",
        )
        self.detail = Gtk.Stack()
        self.detail.set_transition_type(Gtk.StackTransitionType.NONE)
        self.detail.set_vhomogeneous(False)
        self.detail.set_hhomogeneous(False)
        self.detail.add_named(self.data_box, "data")
        self.detail.add_named(self.empty_panel, "empty")

        self.states = ViewStates(vbox(18, kpis, self.detail), noun="report", on_retry=self.reload)
        self.append(header)
        self.append(navigation)
        self.append(self.states)

    @staticmethod
    def _badge(icon: str, kind: str) -> Gtk.Box:
        badge = Gtk.Box()
        badge.add_css_class("kpi-badge")
        badge.add_css_class(kind)
        badge.set_size_request(32, 32)
        badge.set_halign(Gtk.Align.START)
        badge.set_valign(Gtk.Align.START)
        image = Gtk.Image.new_from_icon_name(icon)
        image.set_hexpand(True)
        image.set_halign(Gtk.Align.CENTER)
        badge.append(image)
        # The centered icon expands inside the badge; this keeps that from stretching the badge itself.
        badge.set_hexpand(False)
        return badge

    @staticmethod
    def _kpi_card(title: str, value: Gtk.Label, badge: Gtk.Box) -> Gtk.Box:
        texts = vbox(4, text_label(title, "secondary", wrap=True), value)
        texts.set_hexpand(True)
        card = hbox(12, badge, texts)
        card.add_css_class("card")
        card.add_css_class("kpi-card")
        return card

    def on_shown(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.states.load(self._fetch, self._render)

    def on_scheduler_changed(self) -> None:
        if self.window.current == "report":
            self.states.run_now(self._fetch, self._render)

    def tick(self) -> None:
        pass

    def set_compact(self, compact: bool) -> None:
        pass

    def _today(self) -> date:
        return self.window.app.clock.now().date()

    def set_period(self, period: str) -> None:
        self.period = period
        self.reload()

    def step(self, direction: int) -> None:
        moved = self.anchor + timedelta(days=direction * (7 if self.period == "week" else 1))
        if direction > 0 and moved > self._period_end(self._today()):
            return
        self.anchor = moved
        self.reload()

    def _period_end(self, day: date) -> date:
        if self.period == "week":
            return day - timedelta(days=day.weekday()) + timedelta(days=6)
        return day

    def _fetch(self) -> ReportSummary:
        return self.repo.report(self.period, self.anchor)

    def _render(self, report: ReportSummary) -> None:
        self.report = report
        start, end = date.fromisoformat(report.start_date), date.fromisoformat(report.end_date)
        if self.period == "day":
            self.period_label.set_text(start.strftime("%a, %d %b %Y"))
            self.chart_title.set_text("Focus time per hour")
        else:
            self.period_label.set_text(f"{start:%d %b} to {end:%d %b %Y}")
            self.chart_title.set_text("Focus time per day")
        self.next_button.set_sensitive(self._period_end(self.anchor) < self._period_end(self._today()))

        self.task_value.set_text(str(report.task_completed))
        self.duration_value.set_text(format_duration(report.total_seconds))
        self.states.show_content()
        if not report.logs:
            self.empty_panel.title_label.set_text(EMPTY_TEXT[self.period])
            self.detail.set_visible_child_name("empty")
            return

        if self.period == "day":
            labels = [f"{label}:00" for label in report.bucket_labels]
            self.chart.set_data(labels, report.bucket_seconds, label_every=4)
        else:
            self.chart.set_data(report.bucket_labels, report.bucket_seconds)
        self._render_logs(report.logs)
        self.detail.set_visible_child_name("data")

    def _on_chart_focus(self, index: int | None) -> None:
        if index is None:
            self.chart_focus_label.set_text(
                "Hover the chart or use the Left and Right keys to read each point."
            )
        else:
            self.chart_focus_label.set_text(self.chart.describe(index))

    def _render_logs(self, logs: tuple[FocusLog, ...]) -> None:
        clear_box(self.log_rows)
        for log in logs:
            when = log.ended_at[11:16]
            if self.period == "week":
                when = f"{date.fromisoformat(log.local_date_key):%a} {when}"
            time_label = text_label(when, "secondary", "numeric")
            time_label.set_size_request(72, -1)
            time_label.set_valign(Gtk.Align.START)
            title = text_label(log.title, "body-strong", ellipsize=True)
            origin = log.target_name or ("Timer" if log.source_type == "timer" else "Deleted target")
            texts = vbox(2, title, text_label(origin, "secondary", "small", ellipsize=True))
            texts.set_hexpand(True)
            duration = text_label(format_duration(log.duration_seconds), "numeric")
            if log.subtask_completed:
                status = chip("Completed", "success")
            elif log.source_type == "timer":
                status = chip("Timer")
            else:
                status = chip("Partial", "rest")
            row = hbox(10, time_label, texts, duration, status)
            row.add_css_class("table-row")
            self.log_rows.append(row)

    def log_table(self) -> list[tuple[str, str, str, str, str]]:
        """Visible log rows as (time, title, origin, duration, status)."""
        rows, row = [], self.log_rows.get_first_child()
        while row is not None:
            time_label = row.get_first_child()
            texts = time_label.get_next_sibling()
            duration = texts.get_next_sibling()
            status = duration.get_next_sibling()
            rows.append(
                (
                    time_label.get_text(),
                    texts.get_first_child().get_text(),
                    texts.get_last_child().get_text(),
                    duration.get_text(),
                    status.get_text(),
                )
            )
            row = row.get_next_sibling()
        return rows

    def export(self, kind: str) -> None:
        if self.report is None:
            return
        span = self.report.start_date
        if self.report.end_date != self.report.start_date:
            span += f"_to_{self.report.end_date}"
        self.window.app.file_chooser.save_file(
            self.window,
            f"Export report as {kind.upper()}",
            f"productime-report-{span}.{kind}",
            lambda path: self.export_to(kind, path),
        )

    def export_to(self, kind: str, path: Path) -> None:
        writer = backup_service.write_report_csv if kind == "csv" else backup_service.write_report_json
        try:
            writer(self.report, path)
        except BackupError as error:
            self.window.show_error("Report could not be exported", str(error))
            return
        self.window.toast(f"Report exported to {path.name}")
