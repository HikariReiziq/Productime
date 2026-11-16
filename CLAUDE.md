# CLAUDE.md - Project Engineering & Design Contract for Productime

You are executing the implementation of **Productime**, an ultra-lightweight open-source desktop productivity application built **exclusively for Fedora Linux Desktop** in v1 using **100% Native Python 3 + GTK 4 + libadwaita + Cairo + GStreamer + SQLite3** (no Electron, no Chromium, no WebKit, no Windows/macOS targets, no web/browser runtime).

## Source of Truth Documents
Read these three files completely before writing any application code:
1. `./PRD.md` - Complete functional requirements, user stories, math formulas, and state machines.
2. `./DESIGN.md` - Complete visual tokens, Light/Dark themes, Figma adaptation specs, GTK4 CSS/Cairo styling, and `antislop` (`DURING` mode) rules.
3. `./IMPLEMENTATION_PLAN.md` - Exact Python package layout, SQLite DDL, security requirements, native GTK4/GStreamer architecture, and phased execution plan.

## Verified Local Environment (Fedora 44 Workstation)
- OS: Fedora Linux 44 (GNOME Wayland)
- Runtime & Native Bindings: `python3` 3.14.10, PyGObject (`Gtk` 4.22.5, `Adw` 1.9.4, `Gst` 1.28.9.0 with `playbin`/`mpg123audiodec`/`avdec_mp3`, `Notify` 0.7), `cairo`, `sqlite3` 3.51.2, `DBus` (`org.freedesktop.login1` accessible)
- Dev/Packaging Tools Present: `/home/reiziqzip/.local/bin/ruff`, `python3 -m unittest`, `desktop-file-validate`, `gtk4-update-icon-cache`, `magick`
- Not Installed by Default: `g++`, `rpmbuild`, `cargo`/`rustc`, `pytest`, `flatpak-builder`
- Keep **zero external third-party pip runtime dependencies** so anyone on stock Fedora Linux can clone, test, and run Productime using built-in Fedora system libraries (`python3`, `python3-gobject`, `gtk4`, `libadwaita`, `gstreamer1`, `python3-cairo`).
- Existing assets in `./Assets/`:
  - `Productime Icon.png`
  - `8footdino_on_scratch-alarm.mp3`
  - `biohazard-alarm.mp3`
  - `community-alarm.mp3`
  - `digital-alarm-clock.mp3`
  - `funny-alarm.mp3`
  - `lo-fi-alarm-clock.mp3`

## Mandatory Engineering & Security Rules
- **Ultra-Lightweight Native Desktop Execution**: No Chromium, no WebKit, no background Node servers. Memory usage (`RSS`) must remain compact like a stock GNOME utility (tens of MB, never hundreds of MB), with GStreamer pipelines stopped cleanly when silent and UI animations avoided (`MOTION 1`) when idle or hidden.
- **Unprivileged Execution & File Persistence**: Productime runs strictly under `$USER` using XDG Base Directories (`$XDG_DATA_HOME/productime/`). Every saved Target (with subtasks), Alarm, Timer, and Report entry MUST persist automatically to `~/.local/share/productime/productime.db` (`sqlite3` WAL mode), AND users MUST be able to manually save/export and restore `.json` backups + `.csv`/`.json` reports to disk via native `Gtk.FileDialog`. Never use `sudo`, SUID helpers, or root systemd units.
- **Native UI, Audio & Input Security**:
  - Never pass user strings to `Gtk.Label.set_markup()` (use `set_text()` / `set_label()` to eliminate Pango markup injection) or build SQL strings dynamically (use `?` bound parameters only).
  - Never invoke shell commands (`shell=True`) for audio playback or file operations; play audio strictly in-process via `GStreamer` (`playbin` + `Gst.filename_to_uri`) from verified DB `sounds` records inside `Assets/` or `$XDG_DATA_HOME/productime/sounds/` (extension allowlist, `<= 50 MB`, magic-byte verification, symlink/path-traversal checks).
  - Defend CSV exports against formula injection (`=`, `+`, `-`, `@`, `\t`, `\r` prefixing) with RFC 4180 quoting.
- **No Drift & Continuous Audio**: Anchor timers/stopwatch to `time.monotonic_ns()` paired with `PrepareForSleep` D-Bus signal + wall-delta fallback reconciliation on system wake. Alarm and Timer sounds MUST loop continuously via GStreamer (`SEGMENT_DONE` / seek loop) until dismissed, snoozed, or mission-completed.
- **Target Gate Rule**: A Target Pomodoro batch MUST NOT transition to `resting` until the user explicitly triggers subtask check-off (`✓`). Once `snooze_limit` is reached, Snooze MUST be disabled. Final batch (`i === N`) still enters its scheduled final rest on check-off while showing an immediate `Finish Target / Skip Final Rest` control.

## Mandatory Anti-Slop (`DURING` Mode) Rules
- Use zero em dashes (`U+2014`) anywhere in UI text, documentation, or code comments.
- Both Light and Dark themes must be complete (`Adw.StyleManager` + GTK4 CSS) and pass WCAG AA (`>= 4.5:1` normal text, `>= 3:1` large text/graphics).
- Omit mobile status bars and iOS home indicators from the Figma reference; build a true desktop Left Rail (`>= 900px`) / Bottom Bar (`< 900px`) shell.
- Include explicit Empty, Loading, and Error states on every data view, keyboard navigation (`Tab`, `Enter`, `Escape` on dialogs/overlays), and visible `:focus-visible` outlines.
- Write concise, high-signal comments explaining *why* (not *what*); no decorative comment banners or generic AI filler copy/stats.

## Definition of Done
Testing is 100% **native Python + GTK 4 desktop testing** (no website, no Playwright, no browser server). Before reporting completion:
1. Run `ruff check src tests scripts`, `python3 scripts/check-emdash.py`, `python3 -m unittest discover -s tests/unit -v`, `python3 -m unittest discover -s tests/integration -v`, and `python3 -m unittest discover -s tests/ui -v`.
2. Verify `desktop-file-validate` on generated `.desktop` file, test `scripts/install-local.sh` against a scratchpad `HOME`, run `scripts/build-fedora.sh`, and measure observed real RSS RAM (`ps -o rss=`) of the running Productime window.
3. Re-run all checks after your final edit and report only actually observed command outputs.
