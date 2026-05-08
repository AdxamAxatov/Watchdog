<#
windows_lockdown.ps1 - scorched-earth Windows update + nag suppressor for
Watchdog farming rigs. Drop next to health_check.bat in WatchdogDeploy/Boot/.

Usage (run once, elevated):
    powershell -ExecutionPolicy Bypass -File windows_lockdown.ps1 -Install

That registers two SYSTEM scheduled tasks (at-startup + daily 06:17) which
re-apply the lockdown. Re-running -Install is idempotent.

Other modes:
    -Apply       Run the lockdown now. Used by the scheduled tasks.
    -Uninstall   Remove the scheduled tasks. Does NOT undo registry/service changes.
    -Status      Print current state of services, tasks, and key registry values.
#>

[CmdletBinding(DefaultParameterSetName='Apply')]
param(
    [Parameter(ParameterSetName='Install')]   [switch]$Install,
    [Parameter(ParameterSetName='Apply')]     [switch]$Apply,
    [Parameter(ParameterSetName='Uninstall')] [switch]$Uninstall,
    [Parameter(ParameterSetName='Status')]    [switch]$Status
)

$ErrorActionPreference = 'Continue'  # never abort the whole run on one bad key
Set-StrictMode -Version Latest

# ---------------------------------------------------------------------------
# Paths and logging
# ---------------------------------------------------------------------------

$ScriptPath  = $MyInvocation.MyCommand.Path
$ScriptDir   = Split-Path -Parent $ScriptPath
# Boot/ sits next to logs/ at the deploy root (heartbeat convention).
$DeployRoot  = Split-Path -Parent $ScriptDir
$LogsDir     = Join-Path $DeployRoot 'logs'
$LogPath     = Join-Path $LogsDir 'windows_lockdown.log'

if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null }

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "$ts | $Level | $Message"
    Add-Content -Path $LogPath -Value $line -Encoding utf8
    Write-Host $line
}

function Test-IsAdmin {
    $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object System.Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Set-RegValue {
    param(
        [string]$Path,
        [string]$Name,
        $Value,
        [Microsoft.Win32.RegistryValueKind]$Kind = [Microsoft.Win32.RegistryValueKind]::DWord
    )
    try {
        if (-not (Test-Path $Path)) { New-Item -Path $Path -Force | Out-Null }
        New-ItemProperty -Path $Path -Name $Name -Value $Value -PropertyType $Kind -Force | Out-Null
        Write-Log "reg set: $Path\$Name = $Value"
    } catch {
        Write-Log "reg set FAILED: $Path\$Name = $Value - $($_.Exception.Message)" 'WARN'
    }
}

function Stop-AndDisableService {
    param([string]$Name)
    $svc = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if (-not $svc) { Write-Log "svc skip (not present): $Name"; return }
    try {
        if ($svc.Status -eq 'Running') { Stop-Service -Name $Name -Force -ErrorAction SilentlyContinue }
        # Set-Service -StartupType Disabled doesn't always stick on protected services;
        # editing the registry Start value works even when sc.exe / Set-Service refuse.
        Set-RegValue -Path "HKLM:\SYSTEM\CurrentControlSet\Services\$Name" -Name 'Start' -Value 4
        Write-Log "svc disabled: $Name"
    } catch {
        Write-Log "svc disable FAILED: $Name - $($_.Exception.Message)" 'WARN'
    }
}

function Disable-TasksUnderPath {
    param([string]$TaskPath)  # e.g. '\Microsoft\Windows\WindowsUpdate\'
    try {
        $tasks = Get-ScheduledTask -TaskPath $TaskPath -ErrorAction SilentlyContinue
        if (-not $tasks) { Write-Log "tasks skip (none under): $TaskPath"; return }
        foreach ($t in $tasks) {
            try {
                Disable-ScheduledTask -TaskPath $t.TaskPath -TaskName $t.TaskName -ErrorAction Stop | Out-Null
                Write-Log "task disabled: $($t.TaskPath)$($t.TaskName)"
            } catch {
                Write-Log "task disable FAILED: $($t.TaskPath)$($t.TaskName) - $($_.Exception.Message)" 'WARN'
            }
        }
    } catch {
        Write-Log "task path scan FAILED: $TaskPath - $($_.Exception.Message)" 'WARN'
    }
}

# ---------------------------------------------------------------------------
# Lockdown sections
# ---------------------------------------------------------------------------

function Disable-WindowsUpdate {
    Write-Log '--- Disable-WindowsUpdate ---'

    # Services
    Stop-AndDisableService -Name 'wuauserv'      # Windows Update
    Stop-AndDisableService -Name 'UsoSvc'        # Update Orchestrator (the resurrector)
    Stop-AndDisableService -Name 'DoSvc'         # Delivery Optimization
    Stop-AndDisableService -Name 'WaaSMedicSvc'  # Update Medic - see Disable-WaaSMedic

    # Scheduled tasks
    foreach ($p in @(
        '\Microsoft\Windows\WindowsUpdate\',
        '\Microsoft\Windows\UpdateOrchestrator\',
        '\Microsoft\Windows\WaaSMedic\',
        '\Microsoft\Windows\InstallService\'
    )) { Disable-TasksUnderPath -TaskPath $p }

    # Policy keys: refuse all auto-update behaviour
    $au = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
    Set-RegValue -Path $au -Name 'NoAutoUpdate'          -Value 1
    Set-RegValue -Path $au -Name 'AUOptions'             -Value 1
    Set-RegValue -Path $au -Name 'ScheduledInstallDay'   -Value 0
    Set-RegValue -Path $au -Name 'ScheduledInstallTime'  -Value 0

    $wu = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'
    Set-RegValue -Path $wu -Name 'DisableWindowsUpdateAccess'              -Value 1
    Set-RegValue -Path $wu -Name 'DisableOSUpgrade'                        -Value 1
    Set-RegValue -Path $wu -Name 'DoNotConnectToWindowsUpdateInternetLocations' -Value 1
    Set-RegValue -Path $wu -Name 'ExcludeWUDriversInQualityUpdate'         -Value 1
}

function Disable-WaaSMedic {
    # WaaSMedicSvc is "protected" - Set-Service / sc.exe config refuse to disable
    # it on most builds. Setting the Start value in the registry directly works,
    # but Microsoft also re-enables it via WaaSMedicAgent.exe scheduled remediation.
    # Belt + suspenders: set Start=4 (already done above) AND nuke the scheduled task.
    Write-Log '--- Disable-WaaSMedic ---'
    Disable-TasksUnderPath -TaskPath '\Microsoft\Windows\WaaSMedic\'
    Set-RegValue -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\WaaSMedicSvc' -Name 'Start' -Value 4
}

function Disable-EdgeAutoUpdate {
    Write-Log '--- Disable-EdgeAutoUpdate ---'
    Stop-AndDisableService -Name 'edgeupdate'
    Stop-AndDisableService -Name 'edgeupdatem'
    foreach ($p in @(
        '\',  # Edge tasks live at the root of the task tree, not nested
        '\MicrosoftEdge\'
    )) {
        try {
            Get-ScheduledTask -ErrorAction SilentlyContinue |
                Where-Object { $_.TaskName -like 'MicrosoftEdgeUpdate*' } |
                ForEach-Object {
                    try {
                        Disable-ScheduledTask -TaskPath $_.TaskPath -TaskName $_.TaskName -ErrorAction Stop | Out-Null
                        Write-Log "task disabled: $($_.TaskPath)$($_.TaskName)"
                    } catch {
                        Write-Log "task disable FAILED: $($_.TaskName) - $($_.Exception.Message)" 'WARN'
                    }
                }
        } catch { }
    }
    $e = 'HKLM:\SOFTWARE\Policies\Microsoft\EdgeUpdate'
    Set-RegValue -Path $e -Name 'UpdateDefault'              -Value 0
    Set-RegValue -Path $e -Name 'AutoUpdateCheckPeriodMinutes' -Value 0
    Set-RegValue -Path $e -Name 'InstallDefault'             -Value 0
}

function Disable-StoreAutoUpdate {
    Write-Log '--- Disable-StoreAutoUpdate ---'
    $s = 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore'
    Set-RegValue -Path $s -Name 'AutoDownload'         -Value 2  # off
    Set-RegValue -Path $s -Name 'DisableOSUpgrade'     -Value 1
    Set-RegValue -Path $s -Name 'RemoveWindowsStore'   -Value 0  # keep Store reachable, just no auto
}

function Disable-OOBERelapses {
    # "Finish setting up your PC" / Scoobe - the page that sells you OneDrive,
    # Edge, Microsoft 365 every couple weeks even after dismissed.
    Write-Log '--- Disable-OOBERelapses ---'
    $u = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\UserProfileEngagement'
    Set-RegValue -Path $u -Name 'ScoobeSystemSettingEnabled' -Value 0

    # Default-user hive: applies to any future user profile created on the rig.
    $du = 'Registry::HKEY_USERS\.DEFAULT\SOFTWARE\Microsoft\Windows\CurrentVersion\UserProfileEngagement'
    Set-RegValue -Path $du -Name 'ScoobeSystemSettingEnabled' -Value 0
}

function Suppress-Notifications {
    Write-Log '--- Suppress-Notifications ---'

    # Action Center / notification toasts - machine-wide kill
    $exp = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Explorer'
    Set-RegValue -Path $exp -Name 'DisableNotificationCenter' -Value 1

    # Defender: keep service ON, suppress its notifications
    $def = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows Defender\UX Configuration'
    Set-RegValue -Path $def -Name 'Notification_Suppress' -Value 1

    # Spotlight / tips / "get more out of Windows" - default user hive
    $cdm = 'Registry::HKEY_USERS\.DEFAULT\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager'
    foreach ($n in @(
        'SubscribedContent-338388Enabled',  # Spotlight on lock screen
        'SubscribedContent-338389Enabled',  # tips / suggestions in Settings
        'SubscribedContent-338393Enabled',  # suggested content in Settings
        'SubscribedContent-353694Enabled',
        'SubscribedContent-353696Enabled',
        'SubscribedContent-310093Enabled',
        'RotatingLockScreenEnabled',
        'RotatingLockScreenOverlayEnabled',
        'SystemPaneSuggestionsEnabled',     # Start menu suggestions
        'SilentInstalledAppsEnabled',       # silent app installs (Candy Crush etc)
        'OemPreInstalledAppsEnabled',
        'PreInstalledAppsEnabled',
        'SoftLandingEnabled'
    )) { Set-RegValue -Path $cdm -Name $n -Value 0 }

    # Cloud content policy (machine-wide, applies to all users)
    $cc = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent'
    Set-RegValue -Path $cc -Name 'DisableWindowsConsumerFeatures' -Value 1
    Set-RegValue -Path $cc -Name 'DisableSoftLanding'             -Value 1
    Set-RegValue -Path $cc -Name 'DisableWindowsSpotlightFeatures' -Value 1
    Set-RegValue -Path $cc -Name 'DisableTailoredExperiencesWithDiagnosticData' -Value 1
}

# ---------------------------------------------------------------------------
# Master apply
# ---------------------------------------------------------------------------

function Invoke-WindowsLockdown {
    Write-Log '======== Invoke-WindowsLockdown begin ========'
    Write-Log "Running as: $([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)"
    Disable-WindowsUpdate
    Disable-WaaSMedic
    Disable-EdgeAutoUpdate
    Disable-StoreAutoUpdate
    Disable-OOBERelapses
    Suppress-Notifications
    Write-Log '======== Invoke-WindowsLockdown end ========'
}

# ---------------------------------------------------------------------------
# Scheduled task install / uninstall
# ---------------------------------------------------------------------------

$TaskFolder    = 'Watchdog'
$TaskAtStartup = 'WindowsLockdown_AtStartup'
$TaskDaily     = 'WindowsLockdown_Daily'

function Register-LockdownTask {
    param(
        [string]$Name,
        [Microsoft.Management.Infrastructure.CimInstance]$Trigger
    )
    $action = New-ScheduledTaskAction `
        -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`" -Apply"
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings  = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask `
        -TaskPath ('\{0}\' -f $TaskFolder) `
        -TaskName $Name `
        -Action $action `
        -Trigger $Trigger `
        -Principal $principal `
        -Settings $settings `
        -Force | Out-Null
    Write-Log "task installed: \$TaskFolder\$Name"
}

function Install-Schtasks {
    if (-not (Test-IsAdmin)) {
        Write-Log 'Install requires elevated PowerShell. Aborting.' 'ERROR'
        exit 1
    }
    Write-Log '======== Install-Schtasks begin ========'
    Register-LockdownTask -Name $TaskAtStartup -Trigger (New-ScheduledTaskTrigger -AtStartup)
    Register-LockdownTask -Name $TaskDaily     -Trigger (New-ScheduledTaskTrigger -Daily -At 06:17)
    # Run once now so the rig is locked-down immediately, not at next reboot.
    Invoke-WindowsLockdown
    Write-Log '======== Install-Schtasks end ========'
}

function Uninstall-Schtasks {
    if (-not (Test-IsAdmin)) {
        Write-Log 'Uninstall requires elevated PowerShell. Aborting.' 'ERROR'
        exit 1
    }
    foreach ($n in @($TaskAtStartup, $TaskDaily)) {
        try {
            Unregister-ScheduledTask -TaskPath ('\{0}\' -f $TaskFolder) -TaskName $n -Confirm:$false -ErrorAction Stop
            Write-Log "task removed: \$TaskFolder\$n"
        } catch {
            Write-Log "task remove FAILED: $n - $($_.Exception.Message)" 'WARN'
        }
    }
}

# ---------------------------------------------------------------------------
# Status read-out
# ---------------------------------------------------------------------------

function Show-Status {
    Write-Log '======== Show-Status ========'
    foreach ($s in 'wuauserv','UsoSvc','DoSvc','WaaSMedicSvc','edgeupdate','edgeupdatem') {
        $svc = Get-Service -Name $s -ErrorAction SilentlyContinue
        if ($svc) {
            $start = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\$s" -Name Start -ErrorAction SilentlyContinue).Start
            Write-Log ("svc {0,-14} status={1,-8} startValue={2}" -f $s, $svc.Status, $start)
        } else {
            Write-Log ("svc {0,-14} (not present)" -f $s)
        }
    }
    foreach ($p in '\Microsoft\Windows\WindowsUpdate\','\Microsoft\Windows\UpdateOrchestrator\','\Microsoft\Windows\WaaSMedic\') {
        $tasks = Get-ScheduledTask -TaskPath $p -ErrorAction SilentlyContinue
        if ($tasks) {
            foreach ($t in $tasks) {
                Write-Log ("task {0}{1} state={2}" -f $t.TaskPath, $t.TaskName, $t.State)
            }
        }
    }
    $au = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
    if (Test-Path $au) {
        $v = (Get-ItemProperty $au -ErrorAction SilentlyContinue).NoAutoUpdate
        Write-Log "policy NoAutoUpdate = $v"
    } else {
        Write-Log 'policy NoAutoUpdate = (key absent)'
    }
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

switch ($PSCmdlet.ParameterSetName) {
    'Install'   { Install-Schtasks }
    'Uninstall' { Uninstall-Schtasks }
    'Status'    { Show-Status }
    default     {
        if (-not (Test-IsAdmin)) {
            Write-Log 'Apply requires elevated context (admin or SYSTEM). Aborting.' 'ERROR'
            exit 1
        }
        Invoke-WindowsLockdown
    }
}
