@echo off
setlocal DisableDelayedExpansion
if /I "%~1"=="--skip-tests" (
    shift
)
cd /d "%~dp0"
if errorlevel 1 exit /b 1
if not exist "build\tmp" mkdir "build\tmp"
if errorlevel 1 exit /b 1
set "TEMP=%CD%\build\tmp"
set "TMP=%TEMP%"
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="
set "PYINSTALLER_ZLIB_COMPRESSION_LEVEL=9"
set "PYINSTALLER_CONFIG_DIR=%CD%\build\pyinstaller-cache"

if exist ".venv\Scripts\python.exe" goto dependencies
if not "%~1"=="" goto explicit_python
py -3.12 -m venv ".venv" >nul 2>&1
if not errorlevel 1 goto dependencies
python -m venv ".venv"
if errorlevel 1 goto failed
goto dependencies

:explicit_python
"%~1" -m venv ".venv"
if errorlevel 1 goto failed

:dependencies
set "BUILD_PYTHON=%CD%\.venv\Scripts\python.exe"
powershell -NoProfile -Command "$exePath = Join-Path (Get-Location) 'dist\ZhiXuPDF\ZhiXuPDF.exe'; if (Get-Process -Name ZhiXuPDF -ErrorAction SilentlyContinue | Where-Object Path -EQ $exePath) { Write-Host 'ERROR: Close ZhiXuPDF.exe before building.'; exit 1 }"
if errorlevel 1 goto failed
"%BUILD_PYTHON%" -c "import sys,struct; sys.exit(0 if sys.version_info >= (3,10) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 goto wrong_python
"%BUILD_PYTHON%" "packaging\check_version.py"
if errorlevel 1 goto failed
echo [1/5] Installing verified build dependencies...
"%BUILD_PYTHON%" -m pip install --disable-pip-version-check -r "packaging\requirements-build.txt"
if errorlevel 1 goto failed
"%BUILD_PYTHON%" -m pip check
if errorlevel 1 goto failed

echo [2/5] Building application and document icons...
"%BUILD_PYTHON%" "packaging\make_icon.py"
if errorlevel 1 goto failed

echo [3/5] Checking Python syntax; functional tests are left to the user...
"%BUILD_PYTHON%" -m compileall -q app packaging run.py
if errorlevel 1 goto failed

:package
echo [4/5] Building directory application...
"%BUILD_PYTHON%" -m PyInstaller --noconfirm --clean --workpath "build\onedir" "pdf-manage.spec"
if errorlevel 1 goto failed

echo [5/5] Inspecting the application package...
"%BUILD_PYTHON%" "packaging\verify_exe.py" --skip-runtime
if errorlevel 1 goto failed
:done
echo.
echo Build complete: dist\ZhiXuPDF\ZhiXuPDF.exe
echo Verification report: build\build-report.json
exit /b 0

:wrong_python
echo ERROR: Use 64-bit Python 3.10 or newer. Python 3.12 is recommended.
exit /b 1

:failed
echo.
echo ERROR: Build stopped. The step above failed.
exit /b 1
