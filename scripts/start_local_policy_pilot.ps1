param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Evidence,
    [Parameter(Mandatory = $true)][string]$CodexHome,
    [Parameter(Mandatory = $true)][string]$CodexBinary,
    [switch]$Probe
)

# One manual, limited-current-user task, not a recurring job. Task Scheduler
# owns its lifecycle rather than the desktop turn's child-process job object.
# A registered study is single-attempt even if somebody starts this task twice.
$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pilotPython = Join-Path $projectDir '.venv\Scripts\pythonw.exe'
$studyRoot = (Resolve-Path -LiteralPath $Root).Path
$evidencePath = (Resolve-Path -LiteralPath $Evidence).Path
$codexHomePath = (Resolve-Path -LiteralPath $CodexHome).Path
$codexBinaryPath = (Resolve-Path -LiteralPath $CodexBinary).Path
foreach ($value in @($projectDir, $studyRoot, $evidencePath, $codexHomePath, $codexBinaryPath)) {
    if ($value.Contains('"')) { throw 'Quoted path not supported' }
}
$registration = Get-Content -LiteralPath (Join-Path $studyRoot 'registration.json') -Raw | ConvertFrom-Json
$taskName = 'PAAgent-LocalPilot-' + $registration.registration_digest.Substring(0, 16)
if ($Probe) { $taskName += '-Probe' }
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw 'Task already exists; do not restart or replace it'
}
if ($Probe) {
    $arguments = '-B -c "import time; time.sleep(3)"'
    $limit = New-TimeSpan -Minutes 1
} else {
    if (Test-Path -LiteralPath (Join-Path $studyRoot 'started.json')) {
        throw 'Study already started; no paid retry'
    }
    $arguments = '-B -m pa_agent.experiments.local_policy_pilot --execute --root "' + $studyRoot +
        '" --evidence "' + $evidencePath + '" --codex-home "' + $codexHomePath +
        '" --codex-binary "' + $codexBinaryPath + '"'
    $limit = New-TimeSpan -Hours 13
}
$action = New-ScheduledTaskAction -Execute $pilotPython -Argument $arguments -WorkingDirectory $projectDir
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -Hidden -MultipleInstances IgnoreNew -ExecutionTimeLimit $limit `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output ($taskName + ' started; no automatic restart; remove this exact task after completion')
