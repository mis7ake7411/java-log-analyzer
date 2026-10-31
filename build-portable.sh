#!/bin/sh
set -eu

trap 'result=$?; if [ "$result" -ne 0 ]; then printf "\nBuild failed. Review the error above; no later build steps were run.\n" >&2; fi' 0
CDPATH= cd -- "$(dirname -- "$0")"
project_root=$(pwd -P)

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

[ "$(uname -s)" = Linux ] || fail "Run this script on Linux. Use build-portable.cmd on Windows."
for tool in objdump objcopy; do
    command -v "$tool" >/dev/null 2>&1 || fail "Missing $tool. Install binutils using your distribution's package manager, then rerun."
done

export UV_PROJECT_ENVIRONMENT="$project_root/.venv-portable"
build_python="$UV_PROJECT_ENVIRONMENT/bin/python"

printf '[1/3] Preparing the project-local build environment (Python 3.12)...\n'
command -v uv >/dev/null 2>&1 || fail "uv is required. Install it for your user account from https://docs.astral.sh/uv/getting-started/installation/, then rerun."
if [ -L "$UV_PROJECT_ENVIRONMENT" ]; then
    fail "The selected build environment is a symlink; it was not changed."
fi
if [ -e "$UV_PROJECT_ENVIRONMENT" ]; then
    [ -d "$UV_PROJECT_ENVIRONMENT" ] || fail "The selected build environment is not a directory; it was not changed."
    [ -x "$build_python" ] || fail "The existing build environment has no Python interpreter; it was not changed."
    if ! "$build_python" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))'; then
        fail "The existing build environment must use Python 3.12; it was not changed."
    fi
fi

printf '[2/3] Installing the locked project and portable packaging tools...\n'
uv sync --python 3.12 --locked --extra portable --no-editable
[ -x "$build_python" ] || fail "uv sync did not create the requested Python 3.12 environment."
if ! "$build_python" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))'; then
    fail "uv sync did not create the requested Python 3.12 environment."
fi

printf '[3/3] Building the Linux portable archive...\n'
"$build_python" -B tools/portable/build.py

printf '\nBuild completed. Archives and checksums are in:\n%s/dist/portable\n' "$project_root"
printf 'Extract the new tar.gz before testing. Keep the entire extracted folder.\n'
printf 'Existing extracted applications are not updated automatically.\n'
