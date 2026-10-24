@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1
set "BUILD_PYTHON=%CD%\.venv-portable\Scripts\python.exe"

echo [1/3] Preparing the project-local build environment (Python 3.10 or newer)...
call :ensure_environment
if errorlevel 1 goto failed
"%BUILD_PYTHON%" -c "import sys; sys.exit(sys.version_info < (3, 10))"
if errorlevel 1 goto failed

echo [2/3] Installing this project and the packaging tools...
"%BUILD_PYTHON%" -m pip install . -r tools/portable/requirements-build.txt
if errorlevel 1 goto failed

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

:ensure_environment
if exist "%BUILD_PYTHON%" exit /b 0
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m venv .venv-portable
    exit /b
)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 (
    py -3 -m venv .venv-portable
    exit /b
)
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if errorlevel 1 goto missing_python
python -m venv .venv-portable
exit /b

:missing_python
echo Python 3.10 or newer is required to build this project.
echo Install Python for your user account, or repair the project Python environment.
echo No system Python or PATH settings have been changed.
exit /b 1

:failed
set "BUILD_RESULT=%ERRORLEVEL%"
if "%BUILD_RESULT%"=="0" set "BUILD_RESULT=1"
echo.
echo Build failed. Review the error above; no later build steps were run.
pause
popd
exit /b %BUILD_RESULT%
