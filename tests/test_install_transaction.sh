#!/bin/bash
# Exercise real preparation, switching and rollback with mocked apt/Python/systemd.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURES="$(mktemp -d)"
trap 'result=$?; if [ "$result" != 0 ] && [ -n "${CASE:-}" ]; then cat "$CASE/output" "$CASE/events" >&2; fi; rm -rf -- "$FIXTURES"' EXIT
source <(sed '/^# Main script logic/,$d' "$ROOT/install.sh")

check_root() { :; }
check_fan_hardware() { :; }
flock() { :; }
enable_i2c() { :; }
sleep() { :; }
date() { echo 1700000000; }
chown() { :; }
journalctl() {
    echo "journalctl $*" >> "$CASE/events"
    echo 'mock service log'
    [ "$FAILURE" != diagnostics ]
}
apt-get() {
    echo apt >> "$CASE/events"
    [ "$FAILURE" != apt ]
}
python3() {
    echo python >> "$CASE/events"
    if [ "$FAILURE" = term-prepare ]; then kill -TERM "$BASHPID"; fi
    [ "$FAILURE" != venv ] || return 1
    if [ "${1:-}" = -m ] && [ "${2:-}" = venv ]; then
        for argument in "$@"; do
            if [ "$argument" = --system-site-packages ]; then
                echo 'FAIL: distro packages must not leak into the release venv' >&2
                return 1
            fi
        done
        local venv="${@: -1}"
        mkdir -p "$venv/bin"
        cat > "$venv/bin/python" <<'PYTHON'
#!/bin/bash
echo "venv-python $*" >> "$CASE/events"
if [ "${3:-}" = install ]; then
    case " $* " in *' smbus2 '*) ;; *) echo 'FAIL: isolated INA219 backend is missing' >&2; exit 1 ;; esac
fi
if [ "$FAILURE" = pip ] && [ "${3:-}" = install ]; then exit 1; fi
exit 0
PYTHON
        chmod +x "$venv/bin/python"
    fi
}
systemctl() {
    echo "systemctl $*" >> "$CASE/events"
    case "$1" in
        cat) test -f "$SERVICE_FILE" ;;
        is-active) test "$(cat "$CASE/active")" = true ;;
        is-enabled) test "$(cat "$CASE/enabled")" = true ;;
        stop) echo false > "$CASE/active" ;;
        start)
            if [ -L "$INSTALL_DIR" ] && [ "$(readlink "$INSTALL_DIR")" = "${NEW_RELEASE:-}" ]; then
                if [ "$FAILURE" = start ] || [ "$FAILURE" = recovery ]; then return 1; fi
                if [ "$FAILURE" = health ] || [ "$FAILURE" = diagnostics ]; then echo false > "$CASE/active"; return 0; fi
            fi
            if [ "$FAILURE" = recovery ]; then return 1; fi
            echo true > "$CASE/active"
            ;;
        show)
            case "${4:-}" in
                LoadState) if [ -f "$SERVICE_FILE" ]; then echo loaded; else echo not-found; fi ;;
                MainPID) echo 12345 ;;
                *) echo 0 ;;
            esac
            ;;
        enable) echo true > "$CASE/enabled" ;;
        disable) echo false > "$CASE/enabled" ;;
        daemon-reload)
            if [ "$FAILURE" = term-switch ] && grep -q '/.venv/bin/python' "$SERVICE_FILE"; then kill -TERM "$BASHPID"; fi
            if [ "$FAILURE" = reload ] && grep -q '/.venv/bin/python' "$SERVICE_FILE"; then return 1; fi
            ;;
        status) return 3 ;;
        reset-failed) : ;;
        *) echo "Unexpected systemctl: $*" >&2; return 1 ;;
    esac
}

LOCK_FILE="$FIXTURES/install.lock"

run_case() {
    local name="$1" existing="$2" active="$3" enabled="$4" failure="$5" expected="$6"
    CASE="$FIXTURES/$name"
    mkdir -p "$CASE"
    export CASE FAILURE="$failure"
    INSTALL_DIR="$CASE/app"
    RELEASES_DIR="$CASE/releases"
    SERVICE_FILE="$CASE/app.service"
    HEALTH_FILE="$CASE/health"
    FAN_CLI="$CASE/bin/rpi-fan"
    printf '12345 %s\n' "$(date +%s)" > "$HEALTH_FILE"
    if [ "$failure" = no-stats ]; then rm "$HEALTH_FILE"; fi
    if [ "$failure" = stale-stats ]; then echo '12345 1' > "$HEALTH_FILE"; fi
    if [ "$failure" = wrong-pid ]; then printf '67890 %s\n' "$(date +%s)" > "$HEALTH_FILE"; fi
    SCRIPT_DIR="$ROOT"
    echo "$active" > "$CASE/active"
    echo "$enabled" > "$CASE/enabled"
    : > "$CASE/events"
    if [ "$existing" = legacy ]; then
        mkdir -p "$INSTALL_DIR"
        echo old > "$INSTALL_DIR/marker"
        echo old-unit > "$SERVICE_FILE"
    elif [ "$existing" = release ]; then
        mkdir -p "$RELEASES_DIR/release.old"
        echo old > "$RELEASES_DIR/release.old/marker"
        ln -s "$RELEASES_DIR/release.old" "$INSTALL_DIR"
        echo old-unit > "$SERVICE_FILE"
    fi

    local result=0
    # Use a separate shell with errexit enabled: testing a function in an `if`
    # would disable Bash's errexit semantics inside every installer function.
    set +e
    (
        set -e
        main_install <<< y
    ) > "$CASE/output" 2>&1
    result=$?
    set -e
    if [ "$failure" = start ] || [ "$failure" = health ] || [ "$failure" = diagnostics ]; then
        # `status` returns 3. Logs must still print before the rollback starts,
        # including when journalctl itself fails.
        grep -q 'systemctl status' "$CASE/events"
        grep -q 'journalctl -u rpi_support' "$CASE/events"
        awk '/mock service log/ { logs=1 } /Restoring the previous installation/ { if (!logs) exit 1; restored=1 } END { if (!restored) exit 1 }' "$CASE/output"
        if [ "$failure" = diagnostics ]; then
            grep -q 'Could not read service logs' "$CASE/output"
        fi
    fi
    if [ "$expected" = recovery ]; then
        test "$result" != 0
        test "$(cat "$INSTALL_DIR/marker")" = old
        test "$(cat "$SERVICE_FILE")" = old-unit
        test -n "$(find "$RELEASES_DIR" -maxdepth 1 -name '.transaction.*' -print)"
        test -n "$(find "$RELEASES_DIR" -maxdepth 1 -name 'release.*' -print)"
        grep -q 'Automatic recovery failed' "$CASE/output"
        echo "PASS: $name"
        return
    fi
    if [ "$expected" = success ]; then
        test "$result" = 0
        test -L "$INSTALL_DIR"
        test -x "$INSTALL_DIR/.venv/bin/python"
        test -x "$FAN_CLI"
        test "$(readlink "$FAN_CLI")" = "$INSTALL_DIR/assets/rpi-fan"
        test "$(cat "$CASE/active")" = true
        test "$(cat "$CASE/enabled")" = true
        test ! -e "$RELEASES_DIR/release.old"
        # Every dependency/venv operation precedes the first stop.
        awk '/systemctl stop/ { stopped=1 } /^(apt|python|venv-python)/ { if (stopped) exit 1 }' "$CASE/events"
    else
        test "$result" != 0
        test ! -L "$FAN_CLI"
        test "$(cat "$CASE/active")" = "$active"
        test "$(cat "$CASE/enabled")" = "$enabled"
        if [ "$existing" = none ]; then
            test ! -e "$INSTALL_DIR" && test ! -L "$INSTALL_DIR"
            test ! -e "$SERVICE_FILE"
        else
            test "$(cat "$INSTALL_DIR/marker")" = old
            test "$(cat "$SERVICE_FILE")" = old-unit
        fi
        if [ "$failure" = apt ] || [ "$failure" = venv ] || [ "$failure" = pip ]; then
            ! grep -q 'systemctl stop' "$CASE/events"
        fi
        test -z "$(find "$RELEASES_DIR" -maxdepth 1 -name 'release.*' ! -name release.old -print 2>/dev/null)"
    fi
    test -z "$(find "$RELEASES_DIR" -maxdepth 1 -name '.transaction.*' -print 2>/dev/null)"
    echo "PASS: $name"
}

run_case legacy-update legacy true true none success
run_case release-update release true true none success
run_case first-install none false false none success
run_case apt-failure legacy true true apt failure
run_case venv-failure legacy true true venv failure
run_case pip-failure legacy true true pip failure
run_case reload-failure legacy true true reload failure
run_case startup-failure legacy true true start failure
run_case unhealthy-release release true true health failure
run_case diagnostic-command-failure legacy true true diagnostics failure
run_case missing-statistics legacy true true no-stats failure
run_case stale-statistics legacy true true stale-stats failure
run_case statistics-from-another-process legacy true true wrong-pid failure
run_case stopped-disabled legacy false false health failure
run_case first-start-failure none false false start failure
run_case first-pip-failure none false false pip failure
run_case interrupted-preparation legacy true true term-prepare failure
run_case interrupted-switch legacy true true term-switch failure
run_case recovery-failure legacy true true recovery recovery
run_case install-for-removal none false false none success
remove_installation > "$CASE/removal-output"
test ! -e "$INSTALL_DIR" && test ! -L "$INSTALL_DIR"
test ! -e "$SERVICE_FILE"
test ! -e "$RELEASES_DIR"
test ! -L "$FAN_CLI"
echo 'PASS: removal includes release virtualenvs'
