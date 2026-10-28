@echo off
setlocal enabledelayedexpansion

rem Locate Git Bash (install_swarm.sh targets Git Bash, not WSL).
set "BASH="
for %%B in (
  "%ProgramFiles%\Git\bin\bash.exe"
  "C:\Program Files (x86)\Git\bin\bash.exe"
  "%LOCALAPPDATA%\Programs\Git\bin\bash.exe"
) do if not defined BASH if exist "%%~B" set "BASH=%%~B"

if not defined BASH (
  for /f "delims=" %%G in ('where git 2^>nul') do (
    if not defined BASH if exist "%%~dpG..\bin\bash.exe" set "BASH=%%~dpG..\bin\bash.exe"
  )
)

if not defined BASH (
  >&2 echo Error: Git Bash ^(bash.exe^) not found. Install Git for Windows: https://git-scm.com/download/win
  exit /b 1
)

rem Point Git Bash at the script next to this file, using forward slashes.
set "SCRIPT=%~dp0install_swarm.sh"
set "SCRIPT=!SCRIPT:\=/!"

rem Normalize each argument: backslashes to forward slashes, keep quoting.
set "ARGS="
:loop
if "%~1"=="" goto :run
set "arg=%~1"
set "arg=!arg:\=/!"
set "ARGS=!ARGS! "!arg!""
shift
goto :loop

:run
"%BASH%" "!SCRIPT!" !ARGS!
exit /b !errorlevel!
