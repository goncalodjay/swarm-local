@echo off
setlocal disabledelayedexpansion

rem Windows shim: PowerShell/cmd resolve a bare `<helper>` through PATHEXT, so this
rem .cmd lets agents run the sibling <helper>.sh under Git Bash (never WSL bash).
rem Without it the .sh file association swallows the call and prints nothing.
rem
rem Arguments are forwarded as the raw command line (%*), untouched: no delayed
rem expansion (so `!` survives), no re-parsing loop (so empty arguments and
rem backslashes in free text survive). Known limit: an argument containing a
rem literal double quote is re-split by cmd.exe's own quote rules; avoid `"`
rem inside handoff text passed on the command line.
rem Also unfixable in a .cmd: a bare (unquoted, no spaces) `&`, `|`, `<`, `>` or `^`
rem in an argument, and a newline inside one. Quoted text with spaces is safe;
rem for anything exotic write the text to a file and pass the file.
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

"%BASH%" "%~dpn0.sh" %*
exit /b %ERRORLEVEL%
