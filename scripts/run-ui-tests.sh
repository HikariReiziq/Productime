#!/usr/bin/env bash
# Run the native GTK 4 UI suite inside a private, invisible Wayland session.
#
#   scripts/run-ui-tests.sh                 run tests/ui
#   scripts/run-ui-tests.sh -- CMD ARGS...  run any command in the same headless session
#
# Without mutter or dbus-run-session the command runs on the current display instead.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ "${1:-}" = "--" ]; then
    shift
    COMMAND=("$@")
else
    COMMAND=(python3 -m unittest discover -s tests/ui -v "$@")
fi

for tool in dbus-run-session mutter; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "run-ui-tests: $tool not found, using the current display" >&2
        exec "${COMMAND[@]}"
    fi
done

STATUS_FILE="$(mktemp)"
trap 'rm -f "$STATUS_FILE"' EXIT
export PRODUCTIME_UI_STATUS_FILE="$STATUS_FILE"

# GTK reports a warning when no accessibility bus or registry answers, and the suite fails
# on any GTK warning. A private bus has no systemd to activate them, so start both by hand.
# mutter does not pass the child's exit status through, so the inner shell records it.
INNER='
helpers=()
if [ -x /usr/libexec/at-spi-bus-launcher ]; then
    /usr/libexec/at-spi-bus-launcher --launch-immediately >/dev/null 2>&1 &
    helpers+=($!)
    sleep 0.4
    if [ -x /usr/libexec/at-spi2-registryd ]; then
        /usr/libexec/at-spi2-registryd >/dev/null 2>&1 &
        helpers+=($!)
        sleep 0.3
    fi
fi
mutter --headless --wayland --no-x11 --virtual-monitor 1920x1080 -- \
    sh -c '"'"'"$@"; echo $? > "$PRODUCTIME_UI_STATUS_FILE"'"'"' _ "$@"
for pid in "${helpers[@]}"; do kill "$pid" 2>/dev/null; done
exit 0
'

# Other processes in the session (bus daemon, compositor, portals) log to the same stderr.
# Their lines are dropped here; the suite inspects the test process's own stderr itself.
NOISE='^dbus-daemon\[|^libmutter-|^\(mutter:[0-9]+\): |xdg-desktop-portal|^\*\* Message: .*EGL|^Gdk-Message: .*(Lost connection|Error reading events)|^A connection to the bus|^fusermount|^discover_other_daemon|^$'
dbus-run-session -- bash -c "$INNER" _ "${COMMAND[@]}" 2> >(grep -E -v "$NOISE" >&2) || true
wait

STATUS="$(cat "$STATUS_FILE" 2>/dev/null || true)"
exit "${STATUS:-1}"
