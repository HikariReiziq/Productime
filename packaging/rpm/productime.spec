%global app_id org.productime.Productime

Name:           productime
Version:        %{?version_override}%{!?version_override:1.0.0}
Release:        1%{?dist}
Summary:        Focus timer, alarm, stopwatch, and productivity report for GNOME

License:        MIT
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  desktop-file-utils

# Productime uses only system libraries: there are no pip dependencies to bundle.
Requires:       python3 >= 3.14
Requires:       python3-gobject
Requires:       python3-cairo
Requires:       gtk4
Requires:       libadwaita >= 1.7
Requires:       gstreamer1
Requires:       gstreamer1-plugins-base
Requires:       gstreamer1-plugins-good
Requires:       libnotify
Requires:       hicolor-icon-theme

%description
Productime is a native GTK 4 and libadwaita desktop app for Fedora Linux. It
combines time-blocked Pomodoro targets with a subtask check-off gate, alarms
with typing and math wake-up missions, a stopwatch with lap gaps, timers with
custom looping sounds, and a daily and weekly productivity report. All data is
stored locally in SQLite.

%prep
%autosetup

%build
# Pure Python run from its source tree: nothing to compile.

%install
install -d %{buildroot}%{_datadir}/%{name}
cp -a src/productime %{buildroot}%{_datadir}/%{name}/
find %{buildroot}%{_datadir}/%{name} -type d -name '__pycache__' -prune -exec rm -rf {} +
install -d %{buildroot}%{_datadir}/%{name}/Assets
install -p -m 644 Assets/*.mp3 %{buildroot}%{_datadir}/%{name}/Assets/

install -d %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/%{name} << 'EOF'
#!/bin/sh
PYTHONPATH=%{_datadir}/%{name} exec /usr/bin/python3 -P -s -m productime "$@"
EOF
chmod 755 %{buildroot}%{_bindir}/%{name}

install -d %{buildroot}%{_datadir}/applications
sed 's|@EXEC@|%{name}|g' packaging/%{app_id}.desktop.in \
    > %{buildroot}%{_datadir}/applications/%{app_id}.desktop

install -D -p -m 644 packaging/%{app_id}.search-provider.ini \
    %{buildroot}%{_datadir}/gnome-shell/search-providers/%{app_id}.search-provider.ini
install -d %{buildroot}%{_datadir}/dbus-1/services
sed 's|@EXEC@|%{_bindir}/%{name}|' packaging/%{app_id}.service.in \
    > %{buildroot}%{_datadir}/dbus-1/services/%{app_id}.service

for size in 64 128 256 512; do
    install -D -p -m 644 packaging/icons/hicolor/${size}x${size}/apps/%{app_id}.png \
        %{buildroot}%{_datadir}/icons/hicolor/${size}x${size}/apps/%{app_id}.png
done

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{app_id}.desktop

%files
%license LICENSE
%doc README.md
%{_bindir}/%{name}
%{_datadir}/%{name}/
%{_datadir}/applications/%{app_id}.desktop
%{_datadir}/gnome-shell/search-providers/%{app_id}.search-provider.ini
%{_datadir}/dbus-1/services/%{app_id}.service
%{_datadir}/icons/hicolor/*/apps/%{app_id}.png
