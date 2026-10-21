@echo off
setlocal DisableDelayedExpansion

set "SKIP_SETUP=0"
set "SETUP_ONLY=0"
set "LOCAL_PREVIEW=0"
set "ZHIXUPDF_SETUP_BUILT=0"
set "EXPLICIT_PYTHON="

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--preview" (
    set "LOCAL_PREVIEW=1"
    shift
    goto parse_args
)
if /I "%~1"=="--skip-tests" (
    shift
    goto parse_args
)
if /I "%~1"=="--skip-setup" (
    set "SKIP_SETUP=1"
    shift
    goto parse_args
)
if /I "%~1"=="--setup-only" (
    set "SETUP_ONLY=1"
    shift
    goto parse_args
)
if not defined EXPLICIT_PYTHON (
    set "EXPLICIT_PYTHON=%~1"
    shift
    goto parse_args
)
shift
goto parse_args

:args_done
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

if "%SETUP_ONLY%"=="1" goto build_installer

if exist ".venv\Scripts\python.exe" goto dependencies
if defined EXPLICIT_PYTHON goto explicit_python
py -3.12 -m venv ".venv" >nul 2>&1
if not errorlevel 1 goto dependencies
python -m venv ".venv"
if errorlevel 1 goto failed
goto dependencies

:explicit_python
"%EXPLICIT_PYTHON%" -m venv ".venv"
if errorlevel 1 goto failed

:dependencies
set "BUILD_PYTHON=%CD%\.venv\Scripts\python.exe"
powershell -NoProfile -Command "$exePath = Join-Path (Get-Location) 'dist\ZhiXuPDF\ZhiXuPDF.exe'; if (Get-Process -Name ZhiXuPDF -ErrorAction SilentlyContinue | Where-Object Path -EQ $exePath) { Write-Host 'ERROR: Close ZhiXuPDF.exe before building.'; exit 1 }; if (Get-Process -Name ZhiXuPDF-Setup -ErrorAction SilentlyContinue) { Write-Host 'ERROR: Close ZhiXuPDF-Setup.exe before building.'; exit 1 }"
if errorlevel 1 goto failed
"%BUILD_PYTHON%" -c "import sys,struct; sys.exit(0 if sys.version_info >= (3,10) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 goto wrong_python
if "%LOCAL_PREVIEW%"=="1" (
    "%BUILD_PYTHON%" "packaging\check_version.py" --preview
) else (
    "%BUILD_PYTHON%" "packaging\check_version.py"
)
if errorlevel 1 goto failed
echo [1/6] Installing verified build dependencies...
"%BUILD_PYTHON%" -m pip install --disable-pip-version-check -r "packaging\requirements-build.txt"
if errorlevel 1 goto failed
"%BUILD_PYTHON%" -m pip check
if errorlevel 1 goto failed

echo [2/6] Building application and document icons...
"%BUILD_PYTHON%" "packaging\make_icon.py"
if errorlevel 1 goto failed

echo [3/6] Checking Python syntax; functional tests are left to the user...
"%BUILD_PYTHON%" -m compileall -q app packaging run.py
if errorlevel 1 goto failed

:package
echo [4/6] Building directory application...
"%BUILD_PYTHON%" -m PyInstaller --noconfirm --clean --workpath "build\onedir" "pdf-manage.spec"
if errorlevel 1 goto failed

echo [5/6] Inspecting the application package...
"%BUILD_PYTHON%" "packaging\verify_exe.py" --skip-runtime
if errorlevel 1 goto failed

if "%SKIP_SETUP%"=="1" goto done

:build_installer
echo [6/6] Building setup installer (ZhiXuPDF-Setup.exe)...
if not exist "dist\ZhiXuPDF\ZhiXuPDF.exe" (
    echo ERROR: dist\ZhiXuPDF\ZhiXuPDF.exe not found. Build directory application first.
    goto failed
)
if not exist "dist\ZhiXuPDF\_internal\python*.dll" (
    echo ERROR: dist\ZhiXuPDF\_internal\python*.dll not found.
    goto failed
)
if not exist "packaging\pdf-manage.iss" (
    echo ERROR: packaging\pdf-manage.iss not found.
    goto failed
)

set "ZHIXUPDF_SETUP_COMPILER="
for /f "delims=" %%C in ('where ISCC.exe 2^>nul') do if not defined ZHIXUPDF_SETUP_COMPILER set "ZHIXUPDF_SETUP_COMPILER=%%C"
if not defined ZHIXUPDF_SETUP_COMPILER if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ZHIXUPDF_SETUP_COMPILER=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ZHIXUPDF_SETUP_COMPILER if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ZHIXUPDF_SETUP_COMPILER=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ZHIXUPDF_SETUP_COMPILER if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ZHIXUPDF_SETUP_COMPILER=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ZHIXUPDF_SETUP_COMPILER if exist "%ProgramFiles%\Inno Setup 7\ISCC.exe" set "ZHIXUPDF_SETUP_COMPILER=%ProgramFiles%\Inno Setup 7\ISCC.exe"
if not defined ZHIXUPDF_SETUP_COMPILER if exist "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe" set "ZHIXUPDF_SETUP_COMPILER=%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
if not defined ZHIXUPDF_SETUP_COMPILER if exist "%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe" set "ZHIXUPDF_SETUP_COMPILER=%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe"

:compiler_found
if not defined ZHIXUPDF_SETUP_COMPILER (
    echo WARNING: Inno Setup compiler ISCC.exe not found.
    echo Skipping setup installer build. Install Inno Setup 6 or 7 to build setup.exe.
    goto done
)

if not exist "%ZHIXUPDF_SETUP_COMPILER%" (
    echo ERROR: Compiler does not exist: "%ZHIXUPDF_SETUP_COMPILER%"
    goto failed
)

if not exist "build" mkdir "build"
REM Installers must be compiled directly into this fixed repository directory.
set "ZHIXUPDF_SETUP_OUTPUT=%CD%\dist-installer"
if not exist "%ZHIXUPDF_SETUP_OUTPUT%" mkdir "%ZHIXUPDF_SETUP_OUTPUT%"
if errorlevel 1 goto failed
set "ZHIXUPDF_SETUP_LOG=%CD%\build\setup-compiler.log"

"%ZHIXUPDF_SETUP_COMPILER%" "/O%ZHIXUPDF_SETUP_OUTPUT%" "packaging\pdf-manage.iss" >"%ZHIXUPDF_SETUP_LOG%" 2>&1
set "ZHIXUPDF_SETUP_EXIT=%ERRORLEVEL%"
type "%ZHIXUPDF_SETUP_LOG%"
if not "%ZHIXUPDF_SETUP_EXIT%"=="0" goto setup_failed
findstr /c:"Successful compile" "%ZHIXUPDF_SETUP_LOG%" >nul
if errorlevel 1 goto setup_failed
if not exist "%ZHIXUPDF_SETUP_OUTPUT%\ZhiXuPDF-Setup.exe" goto setup_failed
set "ZHIXUPDF_SETUP_BUILT=1"

:done
echo.
echo ========================================================
echo Build complete:
echo   Application: dist\ZhiXuPDF\ZhiXuPDF.exe
if "%ZHIXUPDF_SETUP_BUILT%"=="1" (
    echo   Installer:   dist-installer\ZhiXuPDF-Setup.exe
)
echo   Report:      build\build-report.json
echo ========================================================
exit /b 0

:wrong_python
echo ERROR: Use 64-bit Python 3.10 or newer. Python 3.12 is recommended.
exit /b 1

:setup_failed
echo.
echo ERROR: Setup installer compilation failed. See log: %ZHIXUPDF_SETUP_LOG%
exit /b 1

:failed
echo.
echo ERROR: Build stopped. The step above failed.
exit /b 1
