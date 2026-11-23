# swarm-kura installer for Windows (PowerShell):
#   irm https://raw.githubusercontent.com/goncalodjay/swarm-local/master/install.ps1 | iex
#
# The swarm runs its scripts under Git Bash, so this only makes sure Git for
# Windows is present, runs install.sh under Git Bash, and puts
# %USERPROFILE%\.local\bin (swarm-kura.cmd) on the user PATH.
$ErrorActionPreference = 'Stop'
$Repo = if ($env:SWARM_KURA_REPO) { $env:SWARM_KURA_REPO } else { 'goncalodjay/swarm-local' }
$Branch = if ($env:SWARM_KURA_INSTALLER_REF) { $env:SWARM_KURA_INSTALLER_REF } else { 'master' }

function Find-GitBash {
  $candidates = @(
    "$env:ProgramFiles\Git\bin\bash.exe",
    "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
    "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
  )
  $git = Get-Command git -ErrorAction SilentlyContinue
  if ($git) { $candidates += (Join-Path (Split-Path (Split-Path $git.Source)) 'bin\bash.exe') }
  foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
  return $null
}

$bash = Find-GitBash
if (-not $bash) {
  Write-Host 'Git for Windows (Git Bash) no está instalado.' -ForegroundColor Yellow
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    $answer = Read-Host '¿Instalarlo ahora con winget? [S/n]'
    if ($answer -eq '' -or $answer -match '^(s|si|sí|y|yes)$') {
      winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
      $bash = Find-GitBash
    }
  }
  if (-not $bash) { throw 'Instalá Git for Windows (https://git-scm.com/download/win) y reintentá.' }
}

$tmp = Join-Path $env:TEMP ("swarm-kura-install-" + [guid]::NewGuid().ToString('N') + '.sh')
Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/$Repo/$Branch/install.sh" -OutFile $tmp
try {
  & $bash $tmp.Replace('\', '/') @args
  if ($LASTEXITCODE -ne 0) { throw "install.sh terminó con código $LASTEXITCODE" }
} finally {
  Remove-Item -Force $tmp -ErrorAction SilentlyContinue
}

$binDir = Join-Path $env:USERPROFILE '.local\bin'
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not ($userPath -split ';' | Where-Object { $_ -ieq $binDir })) {
  [Environment]::SetEnvironmentVariable('Path', ($binDir + ';' + $userPath).TrimEnd(';'), 'User')
  $env:Path = "$binDir;$env:Path"
  Write-Host "Se agregó $binDir al PATH del usuario (abrí una terminal nueva)." -ForegroundColor Green
}
Write-Host ''
Write-Host 'Listo. En tu proyecto:  swarm-kura init   ·   luego:  swarm-kura run' -ForegroundColor Green
