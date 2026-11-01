#!/bin/bash
# Run with: bash tests/test_install_i2c.sh
# All configuration writes target temporary fixtures, never the host's /boot or /etc.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURES="$(mktemp -d)"
trap 'rm -rf -- "$FIXTURES"' EXIT

# Load the real installer functions without dispatching an install command.
# Redirect the hard-coded system paths to fixtures for the fallback tests.
source <(sed '/^# Main script logic/,$d' "$ROOT/install.sh" |
    sed -e "s|/boot/firmware/config.txt|$FIXTURES/config.txt|g" \
        -e "s|/boot/config.txt|$FIXTURES/legacy-config.txt|g" \
        -e "s|/dev/i2c-1|$FIXTURES/i2c-1|g" \
        -e "s|/etc/modules|$FIXTURES/modules|g")

lsmod() { echo 'i2c_dev 1 0'; }
touch "$FIXTURES/i2c-0"
if is_i2c_enabled; then echo 'FAIL: bus 0 is not bus 1'; exit 1; fi
echo 'PASS: bus 0 alone is rejected'
touch "$FIXTURES/i2c-1"
is_i2c_enabled
echo 'PASS: bus 1 is accepted'
rm "$FIXTURES/i2c-1"

for contents in '' '#i2c-dev' '# load i2c-dev here' 'i2c-dev-extra' 'i2c-dev' ' i2c-dev # enabled'; do
    printf '%s\n' "$contents" > "$FIXTURES/modules"
    configure_i2c_module "$FIXTURES/modules"
    cp "$FIXTURES/modules" "$FIXTURES/modules-first-pass"
    configure_i2c_module "$FIXTURES/modules"
    cmp "$FIXTURES/modules" "$FIXTURES/modules-first-pass"
    grep -Eq '^[[:space:]]*i2c-dev([[:space:]]*(#.*)?)?$' "$FIXTURES/modules"
    echo "PASS: module configuration '$contents' (including repeat run)"
done

check_config() {
    local name="$1" contents="$2" expected="$3" actual
    printf '%s' "$contents" > "$FIXTURES/config.txt"
    if is_i2c_in_config "$FIXTURES/config.txt"; then actual=0; else actual=1; fi
    if [ "$actual" != "$expected" ]; then
        echo "FAIL: detection: $name" >&2
        exit 1
    fi
    configure_i2c_file "$FIXTURES/config.txt"
    is_i2c_in_config "$FIXTURES/config.txt"
    cp "$FIXTURES/config.txt" "$FIXTURES/first-pass.txt"
    configure_i2c_file "$FIXTURES/config.txt"
    cmp "$FIXTURES/config.txt" "$FIXTURES/first-pass.txt"
    echo "PASS: $name (including repeat run)"
}

check_config 'empty config' '' 1
check_config 'commented on' $'#dtparam=i2c_arm=on\n' 1
check_config 'commented off' $'#dtparam=i2c_arm=off\n' 1
check_config 'active off' $'dtparam=i2c_arm=off\n' 1
check_config 'global on' $'dtparam=i2c_arm=on\n' 0
check_config 'whitespace and comment' $'  dtparam = i2c_arm = on # enabled\n' 0
check_config 'model-specific on' $'[pi4]\ndtparam=i2c_arm=on\n' 1
check_config 'conditional override after all' $'[all]\ndtparam=i2c_arm=on\n[pi4]\ndtparam=i2c_arm=off\n' 1
check_config 'unconditional override at end' $'[pi4]\ndtparam=i2c_arm=off\n[all]\ndtparam=i2c_arm=on\n' 0
check_config 'invalid on suffix' $'dtparam=i2c_arm=only\n' 1

# Simulate unavailable I2C hardware and raspi-config without touching devices.
is_i2c_enabled() { return 1; }
sleep() { :; }
modprobe() { return 0; }
raspi-config() { return "$RASPI_RESULT"; }
command() {
    if [ "$1" = -v ] && [ "$2" = raspi-config ] && [ "$RASPI_PRESENT" = false ]; then
        return 1
    fi
    builtin command "$@"
}

for scenario in missing failed successful; do
    : > "$FIXTURES/config.txt"
    : > "$FIXTURES/modules"
    RASPI_PRESENT=true
    RASPI_RESULT=0
    case "$scenario" in
        missing) RASPI_PRESENT=false ;;
        failed) RASPI_RESULT=1 ;;
    esac
    enable_i2c > "$FIXTURES/output.txt"
    if [ "$scenario" = successful ]; then
        test ! -s "$FIXTURES/config.txt"
        test ! -s "$FIXTURES/modules"
    else
        is_i2c_in_config "$FIXTURES/config.txt"
        grep -qx 'i2c-dev' "$FIXTURES/modules"
    fi
    echo "PASS: raspi-config $scenario"
done
