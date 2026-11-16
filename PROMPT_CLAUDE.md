# Execution Prompt for Claude

Salin dan tempel prompt di bawah ini ke Claude untuk mengeksekusi Productime versi **100% Native Fedora Linux (Python 3 + GTK 4 + libadwaita + Cairo + GStreamer + SQLite3)** agar ultra-ringan dan irit RAM:

---

```text
Kerjakan implementasi aplikasi desktop **100% native dan ultra-ringan khusus Fedora Linux** "Productime" secara end-to-end di direktori proyek saat ini (/home/reiziqzip/Programmer/Productime). Fokus v1 khusus Fedora Linux Desktop saja, TANPA Electron, TANPA Chromium, TANPA WebKit, dan TANPA server web.

Langkah wajib sebelum mulai ngoding:
1. Baca tuntas file CLAUDE.md, PRD.md, DESIGN.md, dan IMPLEMENTATION_PLAN.md di root repositori.
2. Periksa folder Assets/ (berisi Productime Icon.png dan 6 file .mp3 alarm bawaan) dan jangan ubah atau hapus file aset aslinya.

Aturan Eksekusi:
- Gunakan stack 100% Native Fedora yang sudah terpasang: Python 3 + PyGObject (Gtk 4.0, Adw 1) + GTK4 CSS + Cairo (Gtk.DrawingArea) + GStreamer 1.0 (playbin) + Notify 0.7 / Gio.Notification + sqlite3 stdlib (WAL mode). Nol dependensi runtime pip pihak ketiga dan nol npm/Node.
- Kerjakan bertahap dari Phase 1 sampai Phase 6:
  1) Bootstrap: pyproject.toml (konfigurasi ruff), scripts/check-emdash.py, scripts/run-dev.sh, paths.py, Adw.Application (application_id="org.productime.Productime", GLib.set_prgname("org.productime.Productime")), serta theme.py + base.css/light.css/dark.css sesuai token DESIGN.md.
  2) Domain engine murni di src/productime/domain/ (target_engine, mission_engine, stopwatch_engine, alarm_schedule, validation, formatters) beserta tests/unit/ via python3 -m unittest (termasuk test_contrast.py untuk memverifikasi rasio kontras WCAG AA light.css/dark.css). Kerjakan secara TDD.
  3) Layer SQLite WAL (~/.local/share/productime/productime.db, izin 0700/0600, query berparameter) + backup_service (save/export backup JSON semua data & export Report CSV/JSON anti formula injection ke file pilihan user via Gtk.FileDialog + restore JSON tervalidasi) + audio_service (GStreamer 1.0 seamless loop kontinu, hentikan pipeline ke NULL saat diam untuk menghemat RAM, serta import custom audio dengan magic-byte & realpath/size guard) + scheduler_service (time.monotonic_ns + D-Bus logind PrepareForSleep resume reconciliation) + notification_service, lengkap dengan tests/integration/.
  4) Bangun UI native GTK 4 + libadwaita + Cairo lengkap untuk 5 menu (Today/Target, Alarm, Stopwatch, Timer, Report) sesuai DESIGN.md dalam mode antislop DURING:
     - Light & Dark theme fungsional (Adw.StyleManager + CSS tokens) dengan kontras WCAG AA (>= 4.5:1) dan font tabular-nums. Gunakan label.set_text() (jangan set_markup()) agar aman dari Pango markup injection.
     - Tanpa elemen mobile/iOS status bar; gunakan Left Navigation Rail desktop (>= 900px) & responsive layout (< 900px via Adw.Breakpoint) hingga 420px.
     - Target Timer: dialog + Create Target (2 kolom: form kiri, VerticalTimeline live kanan), preset 25/5 & 50/10 & custom, Long Rest per N batch dengan durasi custom (rumus seragam batch 1..N termasuk batch terakhir; Contoh 1: 6h + 50/10 + 30m per 3 batch = 6h 40m; Contoh 2: 4h + 25/5 + 30m per 4 batch = 4h 50m), input subtask per batch, snooze limit (Unlimited, 1, 2, 3, 5, 10), FocusTargetView (Cairo ProgressRing 240px + timeline live), dan gerbang wajib centang subtask (atau snooze 1/3/5/10/custom) sebelum bisa masuk istirahat (plus Finish Target / Skip Final Rest di rest terakhir).
     - Alarm: dialog + New Alarm (AM/PM, repeat Mon-Sun, nama, 6 MP3 bawaan Assets/ + custom audio + preview, snooze interval 1..30m & snooze limit, Mission Off / Typing max 3x dari pool hingga 10 frasa dengan anti-paste + anti bulk-insert / Math max 3x Easy/Medium/Hard acak) dengan audio GStreamer loop terus-menerus sampai snooze/misi lulus.
     - Stopwatch: state tegas (Start/Lap -> Pause/Lap -> Resume/Clear) + tabel Lap Gap jarak antar lap.
     - Timer: dialog Hours-Minutes-Seconds, nama, pilihan custom music lokal maupun bawaan, dan loop GStreamer terus-menerus saat habis.
     - Report (My Productivity): Task Completed, Time Duration, Day/Week toggle, kurva Bezier Cairo interaktif (hover & keyboard), log subtask persisten SQLite, Export CSV/JSON, serta Save Backup (.json), Restore Backup (.json), dan Open Data Folder.
     - Pastikan nol karakter em dash (U+2014), state Empty/Loading/Error lengkap, dan Escape pada SubtaskGate/AlarmRinging menutup dialog ke alert banner tetap di window (audio & gerbang tetap aktif).
  5) Siapkan scripts/install-local.sh (salin aplikasi + Assets ke ~/.local/share/productime-app/, wrapper ~/.local/bin/productime, icon hicolor ~/.local/share/icons/hicolor/512x512/apps/org.productime.Productime.png, dan ~/.local/share/applications/org.productime.Productime.desktop dengan Terminal=false agar bisa diklik langsung di GNOME App Grid/Dash tanpa terminal, tanpa sudo), packaging/rpm/productime.spec, scripts/build-fedora.sh, README.md, LICENSE (MIT), dan .github/workflows/ci.yml.
  6) Pengujian 100% native desktop (TIDAK pakai website/Playwright/browser): jalankan ruff check src tests scripts, python3 scripts/check-emdash.py, seluruh python3 -m unittest discover (tests/unit, tests/integration, tests/ui GTK 4 interactive tests), desktop-file-validate, uji install-local.sh di HOME sementara, jalankan scripts/build-fedora.sh, dan ukur konsumsi RAM nyata (RSS) proses Productime saat berjalan.

Jangan mengklaim selesai sebelum seluruh test dan lint lulus tanpa error, dan laporkan output perintah serta angka pemakaian RAM yang benar-benar teramati.
```
