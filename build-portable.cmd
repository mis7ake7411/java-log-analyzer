@echo off
setlocal
set "BUILD_RESULT="
pushd "%~dp0"
if errorlevel 1 exit /b 1
set "UV_PROJECT_ENVIRONMENT=%CD%\.venv-portable"
for /f "delims=" %%L in ('dir /A:L /B 2^>nul') do if /I "%%L"==".venv-portable" goto unsafe_environment
set "BUILD_PYTHON=%UV_PROJECT_ENVIRONMENT%\Scripts\python.exe"

"%SystemRoot%\System32\where.exe" uv >nul 2>&1
if errorlevel 1 goto missing_uv

if exist "%UV_PROJECT_ENVIRONMENT%" (
    if not exist "%BUILD_PYTHON%" goto incompatible_environment
    "%BUILD_PYTHON%" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)"
    if errorlevel 1 goto incompatible_environment
)

echo [1/3] Preparing the project-local build environment (Python 3.12)...
echo [2/3] Installing the locked project and portable packaging tools...
call uv sync --python 3.12 --locked --extra portable --no-editable
if errorlevel 1 goto failed
if not exist "%BUILD_PYTHON%" goto invalid_synced_environment
"%BUILD_PYTHON%" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)"
if errorlevel 1 goto invalid_synced_environment

echo [3/3] Building the Windows portable archive...
"%BUILD_PYTHON%" -B tools/portable/build.py
if errorlevel 1 goto failed

echo.
echo Build completed. Archives and checksums are in:
echo "%CD%\dist\portable"
echo Extract the new ZIP before testing. Keep the entire extracted folder.
echo Existing extracted applications are not updated automatically.
pause
popd
exit /b 0

:unsafe_environment
echo The selected .venv-portable path is a symlink or junction; it was not changed.
set "BUILD_RESULT=1"
goto failed

:incompatible_environment
echo The existing portable build environment must use Python 3.12 and contain Scripts\python.exe; it was not changed.
set "BUILD_RESULT=1"
goto failed

:invalid_synced_environment
echo uv sync did not create the requested Python 3.12 environment.
set "BUILD_RESULT=1"
goto failed

:missing_uv
echo uv is required. Install it for your user account from https://docs.astral.sh/uv/getting-started/installation/, then rerun.
set "BUILD_RESULT=1"
goto failed

:failed
if not defined BUILD_RESULT set "BUILD_RESULT=%ERRORLEVEL%"
if "%BUILD_RESULT%"=="0" set "BUILD_RESULT=1"
echo.
echo Build failed. Review the error above; no later build steps were run.
pause
popd
exit /b %BUILD_RESULT%
