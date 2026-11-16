# Productime

A native focus timer, alarm, stopwatch, and productivity report for Fedora Linux.

Productime is written in Python with GTK 4, libadwaita, Cairo, GStreamer, and SQLite. It uses only
libraries that ship with Fedora Workstation: there is no browser engine inside and nothing to
`pip install`.

## What it does

- **Today / Target**: plan a time-blocked Pomodoro target (`25/5`, `50/10`, or custom), add a long
  rest every N batches, and name one subtask per batch. A live vertical timeline shows every work
  block and break before you save. While a target runs, a batch can only move to its break after
  you check off its subtask. If the timer runs out first, the sound loops until you check it off
  or snooze, and the snooze limit you chose is enforced.
- **Alarm**: 12-hour time, repeat days, six bundled sounds or your own audio file, snooze interval
  and limit, and an optional mission to stop it: type 1 to 3 phrases exactly (pasting is blocked)
  or solve 1 to 3 math problems at Easy, Medium, or Hard.
- **Stopwatch**: `Start / Lap`, `Pause / Lap`, `Resume / Clear`, with a table of the gap between
  each lap and the change against the previous lap.
- **Timer**: hours, minutes, and seconds with a name and a sound that loops when time is up.
- **Report**: completed subtasks and focus time per day or week, a chart you can read with the
  pointer or the arrow keys, and CSV or JSON export.

Light and dark themes are included. The window switches from a left rail to a bottom bar below
900 px and stays usable down to 420 px wide.

## Requirements

Fedora Workstation 44 or newer, with these packages (all part of a default GNOME install):

```bash
sudo dnf install python3-gobject gtk4 libadwaita gstreamer1-plugins-good python3-cairo libnotify
```

Python 3.14 or newer and libadwaita 1.7 or newer are required.

## Run it

From a checkout, without installing anything:

```bash
./scripts/run-dev.sh
```

## Install the launcher

```bash
./scripts/install-local.sh
```

This copies the app to `~/.local/share/productime-app/`, writes `~/.local/bin/productime`, and adds
a launcher and icon so Productime opens from the GNOME app grid without a terminal. It never uses
`sudo`. To remove it again (your saved data is kept):

```bash
./scripts/install-local.sh --uninstall
```

## Your data

Everything is saved automatically to a local SQLite database:

```
~/.local/share/productime/productime.db      targets, subtasks, alarms, timers, report log
~/.local/share/productime/sounds/            audio files you imported
```

The folder is readable only by you. From **Data & Backup** in the sidebar you can:

- **Save Backup**: write all targets, alarms, timers, and report entries to one `.json` file.
- **Restore Backup**: replace the current data with a backup file. The file is fully checked
  before anything is changed.
- **Open Data Folder**: show the folder above in Files.

A backup holds your data but not the imported audio files themselves. If a restored item points
to a custom sound that is not on this computer, it falls back to the default alarm sound.

Report exports protect spreadsheet users: cells that start with `=`, `+`, `-`, or `@` are written
as plain text.

## Keyboard

| Keys | Action |
|---|---|
| `Ctrl+1` to `Ctrl+5` | Today, Alarm, Stopwatch, Timer, Report |
| `Ctrl+N` | Create a target, alarm, or timer in the current view |
| `Tab`, `Shift+Tab`, `Enter`, `Space` | Move between and activate controls |
| `Escape` | Close a dialog |
| `Left`, `Right`, `Home`, `End` | Read the points of the report chart |
| `Ctrl+Q` | Quit |

Pressing `Escape` on a ringing alarm, an expired timer, or a batch check-off does not silence it.
The dialog collapses to a bar at the top of the window and the sound keeps looping until you
deal with it.

## Background behaviour

Closing the window while a target, timer, or stopwatch is running, or while any alarm is enabled,
hides the window and keeps Productime running. Open it again from the app grid to bring the
window back. Use `Ctrl+Q` to quit completely.

Alarms ring only while Productime is running. It does not start itself at login.

## Memory use

Productime sets the GTK renderer to Cairo by default, because this low-motion interface does not
need GPU compositing and the Cairo renderer uses far less memory. Measured on Fedora 44 with
`ps -o rss=`: an idle window uses about 84 to 87 MB. The audio pipeline is released whenever no
sound is playing, and nothing repaints while the window is hidden.

To use a GPU renderer instead, set `GSK_RENDERER` yourself, for example `GSK_RENDERER=ngl`.

## Development

```bash
ruff check src tests scripts
python3 scripts/check-emdash.py
python3 -m unittest discover -s tests/unit -v
python3 -m unittest discover -s tests/integration -v
scripts/run-ui-tests.sh
```

The UI suite drives the real GTK application: real windows, real widgets, real GStreamer
pipelines with a silent sink. `scripts/run-ui-tests.sh` runs it inside a private headless Wayland
session, so no windows appear on your desktop. `python3 -m unittest discover -s tests/ui -v` runs
the same suite on your current display.

Layout of the source tree:

```
src/productime/domain/     pure logic: timeline math, session state machine, missions, validation
src/productime/db/         SQLite schema and parameterized queries
src/productime/services/   audio, scheduler, notifications, backup and export
src/productime/ui/         GTK 4 / libadwaita window, views, and Cairo widgets
```

## Packaging

```bash
scripts/build-fedora.sh
```

This writes a source tarball and `SHA256SUMS` to `dist/`. If `rpmbuild` is installed
(`sudo dnf install rpm-build`), it also builds a `noarch` RPM from `packaging/rpm/productime.spec`.

## Known limits

- The interface is in English.
- The typing mission accepts one character per key press. Input methods that commit several
  characters at once are treated like a paste.
- A system clock that is set forward by hand while a countdown runs is treated like time spent
  asleep, so the countdown skips ahead by the same amount.

## License

The source code is released under the MIT License, see [LICENSE](LICENSE).

The icon and alarm sounds in `Assets/` were supplied by the project owner. Check their own terms
before redistributing them.
