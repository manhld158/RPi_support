#!/bin/bash
# A busy lock must block every mutating entry point before any side effect.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURES="$(mktemp -d)"
trap 'rm -rf -- "$FIXTURES"' EXIT
source <(sed '/^# Main script logic/,$d' "$ROOT/install.sh")
SCRIPT_DIR="$ROOT"
LOCK_FILE="$FIXTURES/shared.lock"
check_root() { :; }
flock() {
    test "$1" = -n && test "$2" = 9
    echo "$LOCK_FILE" >> "$FIXTURES/locks"
    return 1
}
systemctl() { touch "$FIXTURES/side-effect"; }
apt-get() { touch "$FIXTURES/side-effect"; }
for operation in main_install remove_installation remove_all restart_service configure_fans; do
    result=0
    set +e
    (set -e; "$operation") > "$FIXTURES/output" 2>&1
    result=$?
    set -e
    test "$result" != 0
    test ! -e "$FIXTURES/side-effect"
    grep -q 'Another install, removal or restart is running' "$FIXTURES/output"
    echo "PASS: busy shared lock blocks $operation"
done
test "$(wc -l < "$FIXTURES/locks")" = 5
test "$(sort -u "$FIXTURES/locks")" = "$LOCK_FILE"
show_usage > "$FIXTURES/help"
grep -q 'Alias for remove (shared dependencies are retained)' "$FIXTURES/help"
echo 'PASS: remove-all help describes retained dependencies'
