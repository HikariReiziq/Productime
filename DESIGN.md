# Design Direction & Specification: Productime

> Mode: **`antislop: DURING`** | Dials: **`ENERGY 2 / RHYTHM 2 / MOTION 1`**
> Design Read: *"Ultra-lightweight native Fedora Linux (GTK 4 / libadwaita) desktop productivity workstation for IT students and developers, in a high-craft Focus-Utility style inspired by TimePad UI Kit and Productime Icon, dial ENERGY 2 / RHYTHM 2 / MOTION 1."*

---

## 1. Visual Identity & Differentiation Anchor

- **Logo & App Mark**: Use [Assets/Productime Icon.png](file:///home/reiziqzip/Programmer/Productime/Assets/Productime%20Icon.png) (white rounded square housing a vibrant cobalt-to-sky open-arc clock face).
- **Differentiation Anchor**: **Split Focus Studio** on desktop: a crisp Cairo circular countdown ring (`240px`) paired directly alongside an interactive **Vertical Batch & Rest Timeline** where every Pomodoro block maps to a concrete subtask with check-off gating.
- **Desktop Adaptation of Figma References (Native GTK 4 + libadwaita + GTK CSS + Cairo)**:
  - Analyzed Figma nodes: `12:40` (Home Light), `29:1067` (Home Dark), `19:320` (Timer Light), `7:2634` (Timer Dark), `8:216` (Report Light), `8:376` (Report Dark).
  - Mobile-only artifacts from Figma (iOS status bar `12:22`, battery/wifi icons, bottom home indicator bar) MUST be omitted completely.
  - Replace mobile framing with an adaptive native desktop shell (`Adw.ApplicationWindow` + custom GTK4 CSS so brand colors stay authentic rather than plain grey Adwaita chrome):
    - `>= 900px` viewport: Left Navigation Rail (`232px` fixed) + fluid Main Workspace (`Adw.Clamp` `max-width: 1120px` inside `Gtk.ScrolledWindow`).
    - `420px - 899px` viewport (`Adw.Breakpoint`): Compact top bar + single-column content + Bottom Navigation Bar (`>= 44px` tap targets, zero horizontal overflow).

---

## 2. Color System & Contrast Tokens (WCAG AA Compliant)

Both **Light Mode** and **Dark Mode** MUST work completely (driven by `Adw.StyleManager` + `light.css` / `dark.css` `Gtk.CssProvider`) and pass WCAG AA contrast (`>= 4.5:1` for normal text, `>= 3:1` for large text `>= 18px` and UI graphics). Verified mathematically in `tests/unit/test_contrast.py`.

### Core Surface & Ink Tokens

| Token | Light Mode Value | Dark Mode Value | Purpose |
|---|---|---|---|
| `bg_canvas` | `#FFFFFF` | `#070417` | Main application canvas (matches TimePad Light/Dark) |
| `bg_surface` | `#FAFAFF` | `#292639` | Primary card, KPI card, and modal surface |
| `bg_surface_elevated` | `#F3F3FC` | `#322E44` | Hover states, nested panels, active timeline step |
| `bg_track` | `#E9E9FF` | `#1E1B2E` | Ring background track & segmented switch track |
| `border_subtle` | `#E4E4F2` | `#3F3C4E` | Hairline structural card/table dividers |
| `text_primary` | `#070417` | `#FFFFFF` | Headings, active timer digits, primary task titles |
| `text_secondary` | `#4A475C` | `#C9C6D8` | Subtitles, timestamps, axis labels (`>= 4.5:1` AA contrast) |
| `text_muted` | `#5F5B73` | `#A7A3BA` | Unit suffixes (`h`, `m`), inactive tab labels |
| `accent_text` | `#1D4ED8` | `#A5B4FC` | High-contrast link/accent text over `#FFFFFF` / `#070417` (`>= 4.5:1`) |

### Brand Accent & Functional Semantics (`R-29`, `R-01`)

- **Primary Focus Accent (1 deliberate accent family)**:
  - Cobalt Primary: `#215BF0` (from `Productime Icon.png`)
  - Focus Violet: `#8B44E3` / `#9B51E0` (from TimePad timer ring & chart curve)
  - Cobalt-to-Violet gradient (`linear-gradient(135deg, #215BF0, #8B44E3)`) is reserved ONLY for:
    1. Active Focus Ring Cairo arc (`FocusTargetView` & `TimerView`).
    2. Primary CTA button (`Create Target`, `New Alarm`, `Start`, `Check Subtask`).
    3. Productivity Chart smooth Bezier curve (`ReportView`).
- **Semantic Status Colors (used strictly for state meaning, never decoration)**:
  - **Completed / Success**: `#07E092` (Light badge: bg `#E6FCF4`, dark ink `#055C3B`; Dark badge: bg `#1E3A36`, text `#2CEAA3`; text on `#07E092` fill MUST use `#070417`, never white)
  - **Rest / Long Rest / Break**: `#FFA656` (Light badge: bg `#FEF5ED`, text `#8A4500`; Dark badge: bg `#3D313A`, text `#FFB46E`)
  - **Work Tag / Urgent / Overdue Gate / Alarm Ringing**: `#FD5B71` (Light badge: bg `#FFEFF1`, text `#A61B32`; Dark badge: bg `#3E2B3E`, text `#FF7A8B`)
  - **Duration Metric Icon**: `#3D4ABA` (white icon inside `#3D4ABA` rounded `8px` badge, matching TimePad `8:216`)

---

## 3. Typography & Spacing Rhythm

- **Font Family**: `"Rubik", "Adwaita Sans", "Cantarell", sans-serif` (using Fedora's native system `Adwaita Sans` / `Cantarell` directly with zero web fonts or heavy font downloads; supports optional system `Rubik` automatically if present).
- **Numeric Discipline**: Apply `font-variant-numeric: tabular-nums;` in GTK4 CSS (and Pango `tnum` font-feature attributes on Cairo drawings) to ALL clocks, countdowns, stopwatch splits, lap gaps, KPI durations, and timeline timestamps so digits never horizontally jitter when ticking.
- **Type Scale**:
  - Page Title: `24px`, weight `500`
  - Section Title (`Today`, `Upcoming Alarms`): `18px`, weight `500`
  - Focus Ring Timer (`32:10`): `44px`, weight `600`, letter-spacing `1px`
  - Hero Timer / KPI Metric: `32px`, weight `500`
  - Card Title / Task Name: `14px-16px`, weight `500`
  - Body / Meta / Tag Chip: `12px-14px`, weight `400`
- **Radius Hierarchy (`R-11`)**:
  - Tag chips: `6px`
  - Segmented pill inner thumb / buttons / inputs: `8px-10px`
  - Task rows & timeline cards: `12px`
  - KPI stat cards, chart container & dialogs: `16px`
  - Circular (`9999px`): ONLY for `44px` category icon wells, `60px` playback action wells (`Pause`, `Quit`), and radio/dot indicators. Never make every card, button, and input pill-shaped.

---

## 4. Screen-by-Screen UI Specifications

### A. Navigation Shell (`main_window.py`)
- **Left Rail (Desktop `>= 900px`)**:
  - Brand Header: `Assets/Productime Icon.png` (`32x32` rounded `8px`) + `Productime` title.
  - 5 Primary Links: `Today`, `Alarm`, `Stopwatch`, `Timer`, `Report`. Active item uses solid surface highlight + cobalt/violet indicator bar.
  - Live Mini Session Indicator (only visible when a Target or Timer is actively running/ringing).
  - Active Alert Recovery Bar (shown automatically when user presses `Escape` to collapse an active Gate/Ringing dialog, allowing one-click reopen while audio stays looping).
  - Footer Controls: Light/Dark Theme Switch, Sound Volume mute/scale, and **Data & Backup** actions (`Save Backup`, `Restore Backup`, `Open Data Folder`).

### B. Today / Target View (`TodayView` + `CreateTargetDialog` + `FocusTargetView`)
- **Hero Active Card** (when a Target is active or paused, or most recent planned Target): Mirrors Figma `12:40` / `29:1067` top card showing large `00:32:10` countdown, colored dot, Target Name, Category Tag, Batch progress (`Batch 2 of 6`), and primary `Resume Focus` / `Open Focus` action.
- **Today's Target List**: Clean rows (`min-height: 76px`) with category icon well (`44px`), Target Name, total projected time (`6h 40m` / `4h 50m`), Category + Batch count chips, and `Play` / `Edit` / `Delete` actions.
- **Empty State (`R-27`)**: `Adw.StatusPage` / clean panel: *"No targets planned for today. Create a time-blocked Pomodoro target to break your work into focused batches."* + `Create Target` CTA.
- **Create Target Dialog (`CreateTargetDialog`, desktop 2-column paned/grid)**:
  - **Left Form**: Target Name, Category Tag (`Work`, `Study`, `Coding`, `Personal`), Target Duration (`Hours`, `Minutes`), Pomodoro Setup (`25/5`, `50/10`, `Custom`), Long Rest Setup (switch + every `N` batches `1x`/`2x`/`3x`/`Custom` + duration `15m`/`20m`/`30m`/`Custom`), Snooze Limit (`Unlimited`, `1`, `2`, `3`, `5`, `10`), `SoundPicker` + Preview, and **Subtask inputs** auto-generated for every batch (`Batch 1`..`Batch N`).
  - **Right Live Vertical Timeline**: Vertical spine connecting Work blocks (Cobalt/Violet), Short Breaks (Subtle Slate), and Long Rests (Amber) with relative start/end minute markers (`00:00 -> 00:50`, `00:50 -> 01:00`, ..., `06:10 -> 06:40` for Example 1, or `04:20 -> 04:50` for Example 2) and projected total badge (`Total Timeline: 6h 40m` / `4h 50m`).
- **Focus Studio View (`FocusTargetView`, adapted from `19:320` & `7:2634`)**:
  - Left Pane: Back button, Target Title + Tag pill, active Subtask badge (`#9B51E0` ring dot + Subtask Name), `240px` Cairo Progress Ring with centered tabular countdown, and `60px` circular controls: `Complete Subtask (✓)`, `Pause/Resume`, and `Quit`.
  - Right Pane: Live **Vertical Batch Timeline** highlighting Completed batches (emerald check), Active batch (cobalt/violet border), Snooze overrun indicator (if any), and Upcoming breaks/rests. Also displays `Finish Target / Skip Final Rest` when in final-batch rest.
- **Subtask Gate Dialog (`SubtaskGateDialog`)**:
  - Appears when batch timer hits `00:00` (while GStreamer loops the selected sound continuously).
  - Prompt: *"Batch N complete. Did you finish `<subtask>`?"*
  - Primary Action: **Check Subtask & Start Break (`✓`)** (or **Check Subtask & Enter Final Rest / Finish Target** if final batch).
  - Snooze Action Group: Quick buttons `1 min`, `3 min`, `5 min`, `10 min`, plus `Custom` minute spinbox + `Snooze` button. Displays remaining snoozes (`2 of 3 snoozes left`); disables Snooze once limit is reached with explicit message *"Snooze limit reached. Complete the subtask to rest or quit target."*

### C. Alarm View (`AlarmView` + `CreateAlarmDialog` + `AlarmRingingDialog`)
- **Alarm List**: Cards showing large `06:30 AM` tabular clock, Alarm Name, repeat pills (`Mon`-`Sun` active days highlighted), sound badge, mission badge (`Typing 2x` / `Math Hard 3x`), next ring relative label, and accessible `Gtk.Switch` + Edit/Delete buttons.
- **Create Alarm Dialog**:
  - Time selector (`Hour 01-12`, `Minute 00-59`, `AM` / `PM` toggle).
  - Weekday selector (`Mon`, `Tue`, `Wed`, `Thu`, `Fri`, `Sat`, `Sun` + quick presets).
  - Alarm Name input.
  - `SoundPicker` listing all 6 `Assets/*.mp3` files + `Import Custom Audio...` (`Gtk.FileDialog`) + `Play / Stop` preview button.
  - Snooze Interval (`1m`, `5m`, `10m`, `15m`, `20m`, `25m`, `30m`) and Snooze Limit (`Unlimited`, `1`, `2`, `3`, `5`, `10`).
  - Mission Mode (`Off`, `Typing` [count `1-3`, shows sample info of active 10-phrase pool], `Math` [count `1-3`, difficulty `Easy` / `Medium` / `Hard`]).
- **Alarm Ringing Dialog**: High-priority modal dialog while GStreamer loops audio continuously. Shows Snooze button (disabled if snooze limit reached), or active Mission UI (`Typing` `Gtk.Entry` with paste shortcuts & bulk insert blocked + `1/3` counter; or `Math` problem card + numeric input + instant error feedback).

### D. Stopwatch View (`StopwatchView`)
- Focal `HH:MM:SS.cc` tabular clock (`56px` desktop).
- Exact button state machine:
  - Idle: `Start` (primary) + `Lap` (insensitive/disabled).
  - Running: `Pause` (secondary) + `Lap` (primary).
  - Paused: `Resume` (primary) + `Clear` (destructive-outline).
- **Lap Table**: Scrollable list with columns `Lap #`, `Lap Gap (Split)` (distance from Lap `k-1` to Lap `k`), `Delta vs Prev` (`-0.42s` / `+1.15s`), and `Total Elapsed`.

### E. Timer View (`TimerView` + `CreateTimerDialog` + `TimerExpiredDialog`)
- Active countdown ring card (`ProgressRingWidget`) + saved timer list/grid with `Start`, `Pause`, `Resume`, `Reset`, `Edit`, `Delete`.
- `+ Add Timer` Dialog: `Hours`, `Minutes`, `Seconds` inputs, Timer Name, `SoundPicker` (`6` bundled MP3s + local `Gtk.FileDialog` music picker + preview), and `Save`.
- Expired Timer Dialog: Loops selected custom music continuously via GStreamer until `Stop Alarm` or `Restart Timer` is clicked.

### F. Productivity Report (`ReportView`, adapted from `8:216` & `8:376`)
- Top Header: `My Productivity` + Period Navigation (`<`, Date/Week label, `>`) + `Export CSV` & `Export JSON` buttons (saving report files directly via native `Gtk.FileDialog`).
- 2 KPI Summary Cards (`132px` min-height, `16px` radius):
  - `Task Completed`: Emerald `#07E092` `32x32` checkmark icon badge (with `#070417` ink check) + count.
  - `Time Duration`: Indigo `#3D4ABA` `32x32` stopwatch icon badge + `Xh Ym` formatted time.
- Segmented Switch: `Day` | `Week`.
- Smooth Productivity Cairo Chart (`320px` height): Dashed horizontal grid lines, smooth Bezier curve in Cobalt-Violet, interactive hover (`Gtk.EventControllerMotion`) & keyboard Left/Right focus bar + concentric marker tooltip showing exact duration per hour (`Day`) or per day (`Week`, `Mon`-`Sun`).
- Completed Subtasks Log Table below chart showing real completed subtasks, target names, durations, and timestamps (`R-17`, `R-38`: zero fabricated numbers; empty state shows clear message when `0` sessions exist for that day/week).

---

## 5. Anti-Slop Purpose-Gate & Mandatory Delivery Checklist (`R-01` to `R-38`)

Every builder (including Claude) MUST verify before finishing:
- **R-02**: Zero em dashes (`U+2014`) anywhere in UI copy, scripts, or comments (`python3 scripts/check-emdash.py`).
- **R-03 & C-4**: Clean layout from `420px` to `1440px+`, no horizontal overflow, `>= 44px` interactive targets.
- **R-17 & R-38**: Zero hardcoded fake KPI stats or demo tasks seeded as user data (use clean empty states on first run).
- **R-25 & R-34**: Both Light and Dark themes fully functional and WCAG AA verified (`tests/unit/test_contrast.py`).
- **R-26 & R-32**: Every button works; all dialogs trap focus and respond to `Escape`; visible `:focus-visible` outlines.
- **R-27**: Explicit Empty, Loading, and Error states on Today, Alarm, Timer, and Report views.
- **R-35**: Native GTK 4 widget/app verification suite performed and recorded after final edit with zero GTK warnings/errors and observed RSS memory logged.
