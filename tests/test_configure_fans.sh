#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURES="$(mktemp -d)"
trap 'rm -rf -- "$FIXTURES"' EXIT
mkdir -p "$FIXTURES/boot/firmware/overlays"
printf 'Raspberry Pi 5 Model B\0' > "$FIXTURES/model"
printf '[pi4]\n# unrelated configuration\n' > "$FIXTURES/boot/firmware/config.txt"
source <(sed '/^# Main script logic/,$d' "$ROOT/install.sh" |
    sed -e "s|/boot/firmware|$FIXTURES/boot/firmware|g" \
        -e "s|/boot/config.txt|$FIXTURES/legacy-config.txt|g" \
        -e "s|/proc/device-tree/model|$FIXTURES/model|g")
SCRIPT_DIR="$ROOT"
LOCK_FILE="$FIXTURES/operation.lock"
FAN_OVERLAY_MARKER="$FIXTURES/active-marker"
check_root() { :; }
flock() { :; }
apt-get() { :; }
DTC_FAIL=false
dtc() {
    [ "$DTC_FAIL" = false ] || return 1
    # This mock checks compiler invocation; actual DTS compilation needs dtc.
    test "$1" = '-@'
    test "$6" = '-o'
    test -f "$8"
    printf 'compiled overlay' > "$7"
}

configure_fans > "$FIXTURES/output"
grep -q '^\[all\]$' "$FIXTURES/boot/firmware/config.txt"
grep -q '^dtoverlay=rpi-support-fans$' "$FIXTURES/boot/firmware/config.txt"
test -s "$FIXTURES/boot/firmware/overlays/rpi-support-fans.dtbo"
cp "$FIXTURES/boot/firmware/config.txt" "$FIXTURES/first-config"
configure_fans > "$FIXTURES/output"
cmp "$FIXTURES/first-config" "$FIXTURES/boot/firmware/config.txt"
echo 'PASS: fan overlay setup is unconditional and idempotent'

DTC_FAIL=true
result=0
(set -e; configure_fans) > "$FIXTURES/output" 2>&1 || result=$?
test "$result" != 0
cmp "$FIXTURES/first-config" "$FIXTURES/boot/firmware/config.txt"
test "$(cat "$FIXTURES/boot/firmware/overlays/rpi-support-fans.dtbo")" = 'compiled overlay'
echo 'PASS: compiler failure preserves previous boot config and overlay'

if check_fan_hardware > "$FIXTURES/output"; then exit 1; fi
touch "$FAN_OVERLAY_MARKER"
check_fan_hardware
echo 'PASS: application install requires the overlay to be active after reboot'
