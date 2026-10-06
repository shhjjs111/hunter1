@echo off
rem ===================================================================
rem  hunter1 一键发布 —— 双击本文件即可
rem
rem  它会：问你要 GitHub 令牌（输入不回显）→ 记住 → 推代码、建
rem  Release、上传产物。令牌只存一次，之后双击直接发布。
rem
rem  为什么整份文件只用 ASCII：cmd.exe 解析 .cmd 时按**当前代码页**读文件，
rem  这里写中文会在非 936 代码页下变成乱码。所有中文提示交给 Python 输出
rem  （gh_publish.py 会自己把控制台切到 UTF-8）。
rem
rem  想先看看要做什么而不真的发布：在终端里跑
rem      release.cmd --dry-run
rem ===================================================================
chcp 65001 >nul
cd /d "%~dp0"
title hunter1 release

set "PY=%~dp0.tools\python\python.exe"
if exist "%PY%" goto :run

rem 项目自带的解释器不在（.tools 没打进来）时，退回 PATH 上的 python。
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
  echo ========== 发布完成 ==========
) else (
  echo ========== 发布失败（退出码 %RC%）==========
)
echo.
pause
