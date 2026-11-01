#!/bin/bash
# Never contacts systemd: all files and service states are temporary fixtures.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURES="$(mktemp -d)"
trap 'rm -rf -- "$FIXTURES"' EXIT
source <(sed '/^# Main script logic/,$d' "$ROOT/install.sh")
LOCK_FILE="$FIXTURES/install.lock"
flock() { :; }

systemctl() {
    echo "$*" >> "$CASE/events"
    case "$1" in
        show)
            [ "$SCENARIO" != query-error ] || return 1
            [ "$SCENARIO" != empty-state ] || return 0
            if [ "$SCENARIO" = missing ]; then echo not-found; else echo loaded; fi
            ;;
        stop)
            # Stop must happen before any installed files are removed.
            test -f "$INSTALL_DIR/marker"
            test -f "$RELEASES_DIR/release.test/marker"
            [ "$SCENARIO" != stop-error ] || return 1
            echo stopped > "$CASE/state"
            ;;
        is-active) return 1 ;; # Simulates the original auto-restart bug.
        is-enabled) [ "$SCENARIO" != missing ] ;;
        disable)
            test "$(cat "$CASE/state")" = stopped
            ;;
        daemon-reload) : ;;
        reset-failed) test "${2:-}" = rpi_support ;;
        status) return 3 ;;
        *) echo "Unexpected systemctl command: $*" >&2; return 1 ;;
    esac
}
journalctl() { echo 'mock service log'; }

for SCENARIO in running auto-restarting loaded-without-file missing stop-error query-error empty-state; do
    CASE="$FIXTURES/$SCENARIO"
    INSTALL_DIR="$CASE/app"
    SERVICE_FILE="$CASE/app.service"
    RELEASES_DIR="$CASE/releases"
    mkdir -p "$INSTALL_DIR" "$RELEASES_DIR/release.test"
    echo keep > "$INSTALL_DIR/marker"
    echo keep > "$RELEASES_DIR/release.test/marker"
    if [ "$SCENARIO" != loaded-without-file ] && [ "$SCENARIO" != missing ]; then
        echo unit > "$SERVICE_FILE"
    fi
    echo "$SCENARIO" > "$CASE/state"
    : > "$CASE/events"
    result=0
    set +e
    (set -e; remove_installation) > "$CASE/output" 2>&1
    result=$?
    set -e
    case "$SCENARIO" in
        stop-error|query-error|empty-state)
            test "$result" != 0
            test -f "$INSTALL_DIR/marker"
            test -f "$RELEASES_DIR/release.test/marker"
            test -f "$SERVICE_FILE"
            ! grep -q '^disable ' "$CASE/events"
            ! grep -q 'has been completely removed' "$CASE/output"
            ;;
        *)
            test "$result" = 0
            test ! -e "$INSTALL_DIR"
            test ! -e "$RELEASES_DIR"
            test ! -e "$SERVICE_FILE"
            if [ "$SCENARIO" = missing ]; then
                ! grep -q '^stop ' "$CASE/events"
            else
                test "$(cat "$CASE/state")" = stopped
                grep -q '^stop rpi_support$' "$CASE/events"
            fi
            ;;
    esac
    echo "PASS: uninstall $SCENARIO"
done
