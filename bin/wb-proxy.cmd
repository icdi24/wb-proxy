@echo off
setlocal
cd /d "%~dp0.."

if "%~1"=="" (
  python scripts\wbctl.py start
) else (
  python scripts\wbctl.py %*
)
if errorlevel 1 (
  echo.
  echo [i] "python" not found, trying py -3 ...
  if "%~1"=="" (
    py -3 scripts\wbctl.py start
  ) else (
    py -3 scripts\wbctl.py %*
  )
)

echo.
pause
