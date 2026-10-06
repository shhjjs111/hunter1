@echo off
rem ===================================================================
rem  hunter1 one-click release -- just double-click this file.
rem
rem  It asks for a GitHub token (input hidden), remembers it, then
rem  pushes the code, creates the Release and uploads the artifacts.
rem  The token is stored outside the repo; later runs skip the prompt.
rem
rem  Why this file is pure ASCII: cmd.exe parses a .cmd file using the
rem  CURRENT console code page, so non-ASCII bytes here can be
rem  mis-decoded on a system whose default is not 936. Every Chinese
rem  message comes from Python instead -- gh_publish.py switches the
rem  console to UTF-8 itself.
rem
rem  To preview without touching anything:
rem      release.cmd --dry-run
rem ===================================================================
chcp 65001 >nul
cd /d "%~dp0"
title hunter1 release

set "PY=%~dp0.tools\python\python.exe"
if exist "%PY%" goto :run

rem Fall back to a python on PATH when the bundled interpreter is absent.
set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found.
  echo   expected: %~dp0.tools\python\python.exe
  echo   or a "python" on PATH
  echo.
  pause
  exit /b 1
)

:run
"%PY%" "%~dp0scripts\gh_publish.py" --create-repo %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo ========== DONE ==========
) else (
  echo ========== FAILED ^(exit %RC%^) ==========
)
echo.
pause
