#!/bin/sh
set -eu

trap 'result=$?; if [ "$result" -ne 0 ]; then printf "\nBuild failed. Review the error above; no later build steps were run.\n" >&2; fi' 0
CDPATH= cd -- "$(dirname -- "$0")"

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

[ "$(uname -s)" = Linux ] || fail "Run this script on Linux. Use build-portable.cmd on Windows."
for tool in objdump objcopy; do
    command -v "$tool" >/dev/null 2>&1 || fail "Missing $tool. Install binutils using your distribution's package manager, then rerun."
done

printf '[1/3] Preparing the project-local build environment (Python 3.12)...\n'
build_python="$PWD/.venv-portable/bin/python"
if [ -x "$build_python" ]; then
    bootstrap="$build_python"
elif [ -x .venv/bin/python ]; then
    bootstrap="$PWD/.venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
    bootstrap=$(command -v python3.12)
elif command -v python3 >/dev/null 2>&1; then
    bootstrap=$(command -v python3)
else
    fail "Python 3.12 with venv support is required. No system packages or PATH settings have been changed."
fi
"$bootstrap" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else "Python 3.12 is required. Recreate the selected virtual environment with Python 3.12; it was not changed.")'
if [ ! -x "$build_python" ]; then
    "$bootstrap" -m venv .venv-portable
fi

printf '[2/3] Installing this project and the packaging tools...\n'
"$build_python" -m pip install . -r tools/portable/requirements-build.txt

printf '[3/3] Building the Linux portable archive...\n'
"$build_python" -B tools/portable/build.py

printf '\nBuild completed. Archives and checksums are in:\n%s/dist/portable\n' "$PWD"
printf 'Extract the new tar.gz before testing. Keep the entire extracted folder.\n'
printf 'Existing extracted applications are not updated automatically.\n'
