@echo off
setlocal DisableDelayedExpansion
chcp 936 >nul
cd /d "%~dp0"
if errorlevel 1 exit /b 1

rem Package the existing EXE only. No downloads, installs or functional tests.
set "ZHIXUPDF_SETUP_COMPILER=%~1"
if defined ZHIXUPDF_SETUP_COMPILER goto check_compiler
for /f "delims=" %%C in ('where ISCC.exe 2^>nul') do if not defined ZHIXUPDF_SETUP_COMPILER set "ZHIXUPDF_SETUP_COMPILER=%%C"
if defined ZHIXUPDF_SETUP_COMPILER goto check_compiler
for %%C in (
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 7\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe"
) do if exist "%%~C" if not defined ZHIXUPDF_SETUP_COMPILER set "ZHIXUPDF_SETUP_COMPILER=%%~C"
if defined ZHIXUPDF_SETUP_COMPILER goto check_compiler
echo 未找到 Inno Setup。请先安装 Inno Setup 编译工具。
set /p "ZHIXUPDF_SETUP_COMPILER=粘贴已安装的 ISCC.exe 完整路径，直接回车取消："
set "ZHIXUPDF_SETUP_COMPILER=%ZHIXUPDF_SETUP_COMPILER:"=%"
if not defined ZHIXUPDF_SETUP_COMPILER goto failed

:check_compiler
if not exist "%ZHIXUPDF_SETUP_COMPILER%" (
    echo 编译器不存在："%ZHIXUPDF_SETUP_COMPILER%"
    goto failed
)
if not exist "dist\ZhiXuPDF\ZhiXuPDF.exe" (
    echo 请先生成 dist\ZhiXuPDF\ZhiXuPDF.exe，再构建安装包。
    goto failed
)
if not exist "dist\ZhiXuPDF\_internal\python*.dll" goto failed
if not exist "packaging\pdf-manage.iss" goto failed
if not exist "build" mkdir "build"
set "ZHIXUPDF_SETUP_STAGE=%CD%\build\setup-%RANDOM%-%RANDOM%"
mkdir "%ZHIXUPDF_SETUP_STAGE%"
if errorlevel 1 goto failed
set "ZHIXUPDF_SETUP_LOG=%ZHIXUPDF_SETUP_STAGE%\compiler.log"
echo 正在将已有 EXE 制作为安装包，请稍候…
"%ZHIXUPDF_SETUP_COMPILER%" "/O%ZHIXUPDF_SETUP_STAGE%" "packaging\pdf-manage.iss" >"%ZHIXUPDF_SETUP_LOG%" 2>&1
set "ZHIXUPDF_SETUP_EXIT=%ERRORLEVEL%"
type "%ZHIXUPDF_SETUP_LOG%"
if not "%ZHIXUPDF_SETUP_EXIT%"=="0" goto compile_failed
rem A terminated compiler may still report exit 0; require the success marker.
findstr /c:"Successful compile" "%ZHIXUPDF_SETUP_LOG%" >nul
if errorlevel 1 goto compile_failed
if not exist "%ZHIXUPDF_SETUP_STAGE%\ZhiXuPDF-Setup.exe" goto compile_failed
if not exist "dist-installer" mkdir "dist-installer"
move /Y "%ZHIXUPDF_SETUP_STAGE%\ZhiXuPDF-Setup.exe" "dist-installer\ZhiXuPDF-Setup.exe" >nul
if errorlevel 1 goto compile_failed
echo.
echo 构建完成：%CD%\dist-installer\ZhiXuPDF-Setup.exe
echo 日志：%ZHIXUPDF_SETUP_LOG%
pause
exit /b 0

:compile_failed
echo.
echo 安装包未构建成功。请检查编译日志及编译错误提示，未替换原有安装包。
echo 日志：%ZHIXUPDF_SETUP_LOG%
:failed
echo 构建停止。
pause
exit /b 1
