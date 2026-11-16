# Product Requirements Document (PRD): Productime

## Problem Statement

Mahasiswa Teknologi Informasi dan developer yang beralih ke **Fedora Linux (GNOME)** untuk perkuliahan dan ngoding membutuhkan sistem alarm, timer, dan manajemen sesi fokus berbasis **Time Blocking + Pomodoro** yang ringan dan dapat diandalkan. Aplikasi bawaan **GNOME Clocks** di Fedora memiliki keterbatasan utama:

1. Tidak dapat memilih musik atau file audio lokal kustom untuk timer dan alarm.
2. Timer hanya berbunyi sekali dalam durasi sangat singkat, sehingga mudah terlewat saat sedang fokus ngoding atau menjauh dari laptop.
3. Tidak mendukung pengulangan audio secara terus-menerus (*continuous loop*) sampai pengguna merespons secara sadar.
4. Tidak menggabungkan target durasi kerja per tugas, pembagian batch Pomodoro dengan *subtask* per batch, *long rest* otomatis, aturan gerbang penyelesaian (*snooze limit* dan centang subtask sebelum istirahat), serta rekap produktivitas harian dan mingguan.

Pengguna juga membutuhkan aplikasi desktop native yang **sangat ringan dan irit RAM** saat berjalan di latar belakang bersama IDE, emulator Android, dan browser, khusus untuk **Fedora Linux** dan siap dipublikasikan ke GitHub.

---

## Solution

**Productime** adalah aplikasi desktop produktivitas native Fedora Linux (`Python 3 + GTK 4 + libadwaita + Cairo + GStreamer + sqlite3`) yang ringan dan memiliki lima modul utama dengan dukungan tema **Light** dan **Dark**:

1. **Today / Target Timer (Time Blocking + Pomodoro)**:
   - Pengguna menekan `+` (*Create Target*) untuk membuat target baru berisi: Nama Target, Tag kategori opsional, total durasi target (`Hours` dan `Minutes`), setup Pomodoro (`25/5`, `50/10`, atau custom bebas), pengaturan *Long Rest* setiap N batch (`1x`, `2x`, `3x`, atau custom dengan durasi istirahat bebas seperti `15m`, `20m`, `30m`, atau custom), serta batas snooze (`Unlimited`, `1`, `2`, `3`, `5`, `10` kali).
   - Sistem menghasilkan **grafik timeline vertikal** interaktif yang memetakan setiap batch kerja, istirahat singkat, dan istirahat panjang beserta akumulasi waktu dan total estimasi selesai, serta input **subtask** untuk setiap batch.
   - Saat dijalankan, timer menggunakan gerbang penyelesaian: pengguna dapat mencentang subtask saat timer masih berjalan atau saat timer habis untuk masuk ke mode istirahat. Jika timer habis dan subtask belum selesai, audio terus berbunyi dan pengguna harus memilih **Snooze** (`1`, `3`, `5`, `10` menit, atau custom) selama kuota snooze masih ada sebelum akhirnya mencentang subtask.
2. **Alarm dengan Misi Bangun**:
   - Pengguna menekan `+` (*New Alarm*) untuk mengatur waktu `AM / PM`, pengulangan hari `Monday` sampai `Sunday`, nama alarm, suara alarm (6 file MP3 bawaan dari folder `Assets/` atau file lokal kustom), interval snooze (`1`, `5`, `10`, `15`, `20`, `25`, `30` menit), batas snooze (`Unlimited`, `1`, `2`, `3`, `5`, `10` kali), dan **Mission Mode**.
   - Mission Mode terdiri dari 2 mode: **Typing** (maksimal 3 kali ketik dari pool hingga 10 frasa acak per sesi) dan **Math** (maksimal 3 soal acak dengan pilihan tingkat kesulitan *Easy*, *Medium*, atau *Hard*).
   - Saat alarm berbunyi, audio diputar secara *loop* terus-menerus oleh GStreamer native hingga disnooze atau misi diselesaikan.
3. **Stopwatch dengan Lap Gap**:
   - State tombol tegas: `Start` + `Lap` saat siap, `Pause` + `Lap` saat berjalan, dan `Resume` + `Clear` saat terjeda.
   - Setiap klik `Lap` mencatat **gap/jarak waktu** antara Lap 1 dan Lap 2, Lap 2 dan Lap 3, dst. beserta selisih delta terhadap lap sebelumnya.
4. **Timer dengan Custom Music**:
   - Pengguna menekan `+` (*Add Timer*) untuk mengatur `Hours : Minutes : Seconds`, nama timer, serta musik/suara pilihan (6 MP3 bawaan atau file audio lokal dari laptop) yang berbunyi *looping* terus-menerus hingga dimatikan.
5. **Summary / Report (`My Productivity`)**:
   - Menyimpan seluruh subtask yang diselesaikan dan total durasi kerja aktual ke SQLite lokal, lalu menampilkannya dalam ringkasan **Day** dan **Week** (`Task Completed`, `Time Duration`, grafik tren Cairo, dan riwayat subtask).

---

## Bundled Project Assets (Verified Local Files)

Aset berikut sudah tersedia di folder `Assets/` pada root repositori:

- Icon Aplikasi: `Assets/Productime Icon.png`
- Audio Bawaan (6 file MP3):
  1. `Assets/8footdino_on_scratch-alarm.mp3` (~23.0 detik)
  2. `Assets/biohazard-alarm.mp3` (~50.0 detik)
  3. `Assets/community-alarm.mp3` (~6.1 detik)
  4. `Assets/digital-alarm-clock.mp3` (~16.0 detik)
  5. `Assets/funny-alarm.mp3` (~9.5 detik)
  6. `Assets/lo-fi-alarm-clock.mp3` (~60.0 detik)

---

## User Stories

### Target Timer & Time Blocking (`Today / Target`)
1. As a Fedora student/developer, I want to click a `+` (Create Target) button on the Today screen, so that I can plan a time-blocked Pomodoro goal.
2. As a user, I want to enter a Target Name, optional category tag, and target duration in Hours and Minutes, so that my goal reflects my real study or coding session.
3. As a user, I want to pick Pomodoro presets (`25m work / 5m break`, `50m work / 10m break`) or custom work and short-break minutes, so that my focus intervals match my task rhythm.
4. As a user, I want to configure a Long Rest after every N batches (`1x`, `2x`, `3x`, or custom N) with any custom duration in minutes (e.g. `15m`, `20m`, `30m`, or custom), so that I recover properly across multi-hour sessions.
5. As a user, I want the app to generate a vertical timeline graphic showing work batches, short breaks, long rests, cumulative timestamps, and total end duration (matching Example 1: `6h` / `50-10` / `30m every 3` -> `6h 40m`, and Example 2: `4h` / `25-5` / `30m every 4` -> `4h 50m`), so that I can verify my schedule before saving.
6. As a user, I want to fill in a subtask name for every generated batch (e.g. Batch 1: Membuat PRD, Batch 2: Membuat Desain, Batch 3: Coding Backend, Batch 4: Coding Database dan Frontend, Batch 5: Security dan Deploy, Batch 6: Finish Selesai Semua), so that every batch has a concrete deliverable.
7. As a user, I want to set a Snooze Limit (`Unlimited`, `1 times`, `2 times`, `3 times`, `5 times`, `10 times`) in the Create Target modal, so that I constrain procrastination per batch/target.
8. As a user, I want to save a Target and launch it anytime via Play on the Today screen, so that I can reuse or execute prepared plans immediately.
9. As a user, I want to see the active Focus view showing Target Name, tag, active Subtask title, circular countdown ring, and live position on the Vertical Timeline, so that I stay oriented during deep work.
10. As a user, I want to check off (`✓`) the active subtask either while the timer is still running or after the batch timer finishes, so that finishing early lets me move directly into Rest mode.
11. As a user, I want the batch-end alarm sound to loop continuously and ask whether the subtask is fulfilled or needs Snooze (`1m`, `3m`, `5m`, `10m`, or custom minutes), so that I cannot enter Rest mode until the subtask is checked off.
12. As a user, I want the Snooze control to lock once the configured Snooze Limit is exhausted, so that I must check off the subtask or explicitly quit the session.
13. As a user, I want checking off the final batch's subtask to enter the final scheduled rest (with an immediate `Finish Target / Skip Final Rest` option) and record the achievement into my Productivity Report, so that my progress is never lost.

### Alarm & Wake-Up Missions
14. As a user, I want to click `+` (New Alarm) and configure time with Hour, Minute, and `AM`/`PM`, so that alarms are intuitive to set.
15. As a user, I want to select repeat days from `Monday` to `Sunday`, so that recurring alarms trigger on the exact weekdays I choose.
16. As a user, I want to name each alarm, so that the ringing screen tells me what the alarm is for.
17. As a user, I want to pick from the 6 bundled MP3 files in `Assets/` (with inline Play/Stop preview) or import a custom local audio file, so that I always have a clear, pleasant, or loud sound.
18. As a user, I want to set Snooze Interval (`1`, `5`, `10`, `15`, `20`, `25`, `30` minutes) and Snooze Limit (`Unlimited`, `1`, `2`, `3`, `5`, `10` times), so that snoozing behaves predictably.
19. As a user, I want to configure an optional Dismissal Mission choosing between two modes: `Typing` (`1` to `3` repetitions from up to `10` active session phrases drawn from a large curated bank) and `Math` (`1` to `3` random problems at `Easy`, `Medium`, or `Hard` difficulty), so that I wake up fully before dismissing the alarm.
20. As a user, I want Easy math to cover addition and subtraction, Medium math to add multiplication, and Hard math to use larger numbers across addition, subtraction, and multiplication, so that challenge difficulty scales meaningfully.
21. As a user, I want ringing alarm audio to loop continuously until I snooze or pass the entire Mission challenge, so that the alarm never dies after one chime.
22. As a user, I want to enable/disable, edit, and delete alarms in the Alarm list and see when the next active alarm will ring, so that schedule management stays effortless.

### Stopwatch
23. As a user, I want `Start` and `Lap` controls before starting, switching to `Pause` and `Lap` while running, and `Resume` and `Clear` when paused, so that button states are unambiguous.
24. As a user, I want clicking `Lap` to record and clearly display the split gap between Lap 1 and Lap 2, Lap 2 and Lap 3, and every subsequent lap (plus delta vs previous lap and total elapsed time), so that interval comparisons are immediate.

### Timer
25. As a user, I want to click `+` (Add Timer) to configure `Hours`, `Minutes`, and `Seconds`, a timer name, and custom music (bundled or local audio file), and save it persistently.
26. As a user, I want saved timers to support start, pause, resume, reset, edit, and delete from the Timer screen.
27. As a user, I want an expired timer to loop its custom music continuously and trigger a Linux desktop notification until dismissed or restarted.

### Productivity Report & Lightweight Desktop Experience
28. As a user, I want every saved Target (with all batches and subtasks), Alarm, Timer, custom audio reference, and completed Report log (`focus_logs`) automatically persisted to a real local SQLite database file (`~/.local/share/productime/productime.db`), so that my saved data survives app restarts and laptop reboots.
29. As a user, I want to manually save/export my Alarms, Targets, Timers, and Report logs to a portable JSON backup file (and import/restore from that JSON file via native Fedora file dialog), as well as export my Report as CSV/JSON, so that I own my data files easily.
30. As a user, I want a `Report` (`My Productivity`) screen showing `Task Completed`, `Time Duration`, a `Day` / `Week` segmented switch, a visual productivity chart, and a completed-subtasks log.
31. As a user, I want a lightweight native GTK 4 desktop app using minimal RAM (no heavy embedded web browser), a working Light and Dark mode toggle, full keyboard accessibility (`Tab`, `Enter`, `Escape`), background timer/alarm resilience when closing the window, and a standalone GNOME App Grid launcher (`Terminal=false`).

---

## Implementation Decisions

### 1. Native Fedora Stack & Lightweight Process Architecture
- **Desktop Target Scope (Fedora Linux Only v1)**:
  - Productime v1 is built exclusively as an ultra-lightweight, offline-first native Linux desktop application targeting **Fedora Linux Workstation (GNOME Wayland/X11, `x86_64` & `noarch` Python)**.
- **Native Stack**: **Python 3 (`3.14+`) + PyGObject (`Gtk 4.0`, `Adw 1`) + GTK4 CSS + `cairo` + `GStreamer 1.0` (`Gst 1.0`) + `Notify 0.7` / `Gio.Notification` + Python stdlib `sqlite3` (WAL mode)**.
  - Zero Chromium, zero WebKit, zero Node/npm runtime dependencies. Uses libraries already present on Fedora Workstation so app memory remains compact (tens of MB rather than hundreds of MB).
- **Clean Layered Internal Architecture**:
  - `productime.domain`: Pure Python modules (zero GTK/DB/IO imports) for math, state machines, formatting, and validation.
  - `productime.db`: Parameterized `sqlite3` WAL repository in `$XDG_DATA_HOME/productime/productime.db` (`0700` dir, `0600` db).
  - `productime.services`: Native GStreamer audio + custom importer, monotonic GLib + `logind` sleep/wake scheduler, notification service, and JSON/CSV backup service.
  - `productime.ui`: `Adw.Application` (`org.productime.Productime`) + adaptive GTK 4 / libadwaita views + custom Cairo widgets.
  - Runs strictly unprivileged in `$USER` space (`$XDG_DATA_HOME/productime/`), never requiring `sudo` or root services.

### 2. Target Batch Calculation & Gated State Machine
```python
class TargetPhase(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    PAUSED_WORK = "paused_work"
    AWAITING_SUBTASK_GATE = "awaiting_subtask_gate"
    SNOOZING = "snoozing"
    RESTING = "resting"
    PAUSED_REST = "paused_rest"
    COMPLETED = "completed"

@dataclass(frozen=True)
class TimelineSlot:
    batch_index: int
    subtask_title: str
    work_minutes: int
    break_minutes: int
    break_type: Literal["none", "short", "long"]
    cumulative_start_minute: int
    cumulative_end_minute: int
```
- **Timeline Calculation Rule**:
  - `target_minutes = target_hours * 60 + target_mins`.
  - `cycle_minutes = work_minutes + short_break_minutes`.
  - Full cycles `full_count = target_minutes // cycle_minutes`, remainder `rem = target_minutes % cycle_minutes`.
  - If `rem > 0`, append one final partial batch with `work_minutes = max(1, min(work_minutes, rem))`; otherwise `batch_count = max(1, full_count)`.
  - Uniform per-batch rest assignment for every batch `i` (`1 <= i <= batch_count`, including the final batch):
    - If `long_rest_enabled and i % long_rest_every == 0`: `break_type = "long"` and `break_minutes = long_rest_minutes` (replaces `short_break_minutes` at that boundary).
    - Else if `short_break_minutes > 0`: `break_type = "short"` and `break_minutes = short_break_minutes`.
    - Else: `break_type = "none"` and `break_minutes = 0`.
  - Runtime final-batch UX: checking off (`✓`) the final batch subtask marks that subtask completed and enters the final rest slot (matching the timeline projection), while showing an immediate **Finish Target / Skip Final Rest** action so the user can complete right away or finish after the final rest.
  - **Verification of User Examples**:
    - *Example 1 ("Website Profile Company")*: `target_minutes = 360` (`6h`), `work_minutes = 50`, `short_break_minutes = 10` -> `6 batches` (`6 x 60m = 360m`), subtasks: `1) Membuat PRD`, `2) Membuat desain`, `3) Coding backend`, `4) Coding database dan frontend`, `5) Security dan deploy`, `6) Finish selesai semua`. With `long_rest_enabled = True`, `long_rest_every = 3`, `long_rest_minutes = 30`: Batch 3 and Batch 6 each replace `10m` with `30m` (`+20m` x 2 = `+40m`), yielding `400m` = **`6h 40m`**.
    - *Example 2 ("Belajar Pemrograman Mobile")*: `target_minutes = 240` (`4h`), `work_minutes = 25`, `short_break_minutes = 5` -> `8 batches` (`8 x 30m = 240m`), subtasks: `1) Belajar fundamental pemrograman mobile`, `2) Membuat JavaScript`, `3) Coding JavaScript`, `4) Coding XML`, `5) Belajar coding JavaScript tingkat lanjut`, `6) Belajar Flutter`, `7) Belajar deploy dan menggunakan Android Studio`, `8) Finish`. With `long_rest_enabled = True`, `long_rest_every = 4`, `long_rest_minutes = 30`: Batch 4 and Batch 8 each replace `5m` with `30m` (`+25m` x 2 = `+50m`), yielding `290m` = **`4h 50m`**.
- **Subtask Gate Invariant**:
  - `resting` cannot be entered from `working`, `snoozing`, or `awaiting_subtask_gate` until the user explicitly triggers `COMPLETE_SUBTASK`.
  - `SNOOZE(minutes)` is allowed only when `snooze_limit is None or batch.snooze_count < snooze_limit`.
  - Both initial work duration and snooze work duration count toward logged focus time in `focus_logs`.

### 3. Native Audio, Scheduler & Fedora Background Behavior
- All countdowns use `time.monotonic_ns()` anchored with `time.time_ns()` wall-clock deltas and systemd-logind `PrepareForSleep` D-Bus resume signals (over `Gio.bus_get_sync`) to reconcile laptop suspend/sleep accurately.
- Audio plays directly in-process via `GStreamer 1.0` (`playbin` over PipeWire/PulseAudio) with seamless continuous loop handling until dismissed, snoozed, or mission-completed; pipeline is moved to `Gst.State.NULL` whenever silent to release audio buffers and minimize RAM.
- Custom audio import validates file extension allowlist (`.mp3`, `.wav`, `.ogg`, `.flac`, `.m4a`), max file size (`50 MB`), `stat.S_ISREG()`, `os.path.realpath()`, and audio magic bytes before copying into `$XDG_DATA_HOME/productime/sounds/<uuid>.<ext>` (`0600`).
- Closing the window when active timers/alarms exist hides the window cleanly (`window.set_visible(False)` / `hide-on-close`) without terminating the `Adw.Application` scheduler; desktop notifications (`Gio.Notification` / `Notify 0.7`) expose click-to-open actions to present the window immediately.

### 4. Data Contracts (`SQLite` Logical Model)
- `sounds`: Bundled and user-imported sound metadata (`id`, `display_name`, `source_type`, `file_path`, `duration_seconds`).
- `targets`: Target setup, Pomodoro parameters, long-rest rules, snooze limit, sound reference, active status.
- `target_batches`: Batch index, subtask title, planned work/break durations, break type, actual work seconds, snooze count, snooze seconds, completion status.
- `alarms`: Name, 24h hour/minute, weekday repeat JSON, sound ID, snooze interval, snooze limit, mission config (`off | typing | math`, count `1..3`, difficulty), enabled flag.
- `timers`: Name, duration seconds, sound ID.
- `focus_logs`: Source type (`target_batch | timer`), title, tag, timestamps, duration seconds, `subtask_completed` flag, local `YYYY-MM-DD` date key.
- `app_preferences`: Theme (`light | dark`), language (`en | id`), `close_to_background`, volume.

---

## Testing Decisions

- **Desktop-Only Native Testing Philosophy (Zero Website / Browser Testing)**: Because Productime is a native GTK 4 desktop application (no HTML/Electron server), testing runs natively via Python (`python3 -m unittest`), `ruff`, and GTK 4 / `Adw` UI interaction suites rather than web browsers or Playwright.
- **Test Suites**:
  1. **Target & Pomodoro Engine (`tests/unit/test_target_engine.py`)**: Verify exact `6h 40m` (Example 1) and `4h 50m` (Example 2) vertical timeline projections under the uniform per-batch formula, uneven remainder batches, early subtask check-off, timer-expiry gate blocking rest without check-off, final-batch rest with immediate **Finish Target / Skip Final Rest**, custom snooze intervals, and strict enforcement of `snooze_limit` (`Unlimited`, `1`, `2`, `3`, `5`, `10`).
  2. **Alarm Scheduler & Mission Engine (`tests/unit/test_alarm_schedule.py`, `tests/unit/test_mission_engine.py`)**: Verify AM/PM conversion, weekday repeat scheduling, per-minute idempotency, snooze limits, Typing pool (`<= 10` session phrases, `1..3` count, exact match), and Math difficulty constraints (`easy`, `medium`, `hard`, `1..3` count).
  3. **Stopwatch Engine & Contrast Math (`tests/unit/test_stopwatch_engine.py`, `tests/unit/test_contrast.py`)**: Verify state transitions (`Start/Lap` -> `Pause/Lap` -> `Resume/Clear`), split gap math, and WCAG AA contrast ratios across `light.css` and `dark.css`.
  4. **SQLite, Backup & Security Boundaries (`tests/integration/`)**: Verify CRUD round-trips, `Day` and `Week` report aggregations, backup JSON export/import round-trips, anti-formula CSV export, path traversal/magic-byte rejection on audio import, and sleep/resume clock reconciliation.
  5. **Native GTK 4 App & RAM Check (`tests/ui/test_gtk_app.py`)**: Instantiate real GTK 4 window/views, exercise navigation across all 5 views, create Target `6h 40m` / `4h 50m`, verify Subtask Gate blocking, Alarm mission validation + paste guard, Stopwatch Lap table, Timer, Light & Dark theme switch, `Escape` handling, and measure real RSS RAM footprint.

---

## Out of Scope

- Electron, Chromium, WebKit, web-browser deployment, Windows, macOS, Android, or iOS builds in v1 (v1 targets **Fedora Linux Desktop** exclusively).
- Cloud accounts, user authentication, or multi-device cloud sync.
- External calendar integrations (Google Calendar, CalDAV) or team collaboration.
- Bypassing Wayland OS lock screens (alarms/timers run continuously in the unlocked user session and deliver PipeWire audio + desktop notifications).
- Official Fedora Copr/Koji repository submission in v1 (v1 distributes via GitHub Releases `.tar.gz`/`.rpm` spec and local desktop installer script).

---

## Further Notes

- Repository currently contains only `Assets/` (`Productime Icon.png` and 6 `.mp3` files) and planning markdown files.
- On stock Fedora GNOME Workstation without the AppIndicator extension, native `Gio.Notification` actions (`Open Productime`) + `Adw.Application` `hide-on-close` serve as the reliable background restore mechanism.
