# windows_lockdown.ps1 — Test Runbook

Manual test plan. Read top-to-bottom; do not skip Tier 0.

> **Order of operations:** prove it on a VM (Tiers 0-5), then one pilot rig
> for one week (Tier 6), then fleet. Do **not** install on a critical
> farming rig before Tier 5 passes on a throwaway machine.

All commands assume an **elevated PowerShell** (Run as Administrator) and
the script at `<deploy>\Boot\windows_lockdown.ps1` with `<deploy>\logs\`
sibling to it (matches the heartbeat convention). Adjust `cd` accordingly.

### Where things get logged

After every `-Apply` (whether you ran it manually or the daily timer did),
three files in `..\logs\` are updated. Glance at these to know if it's
working without scrolling through chatter:

| File | What it tells you | When it changes |
|---|---|---|
| `windows_lockdown_status.txt` | **Most recent verdict.** One block. "CLEAN 16/16" / "DRIFT_FIXED" / "PARTIAL_FAIL" / "HARD_FAIL", with per-check OK/BROKEN list. | Overwritten each `-Apply` run |
| `windows_lockdown_history.log` | **Trend over time.** One line per run: `<ts> | VERDICT | <compliant>/<total> | broken=[...]`. Tail it to scan a week of runs in seconds. | Appended each `-Apply` run |
| `windows_lockdown.log` | **Full chatter** — every reg set, svc disable, task disable, with `WARN`/`FAILED` lines for diagnostics. | Appended each run (any mode) |

Verdicts:
- `CLEAN` — nothing was broken pre-run, nothing changed. Steady state.
- `DRIFT_FIXED` — Windows resurrected something between daily runs; we re-disabled it. **Expected** behavior on the daily timer.
- `PARTIAL_FAIL` — some checks remained broken even after the apply. Needs attention.
- `HARD_FAIL` — apply didn't land at all (admin? script error?).

You can also just run `-Status` any time — it now prints the current
compliance score, the last status snapshot, and the last 10 history
entries.

---

## Tier 0 — Pre-flight (capture baseline)

Run before installing. Saves you from "wait, was that already broken?".

```powershell
cd <deploy>\Boot
.\windows_lockdown.ps1 -Status | Tee-Object -FilePath ..\logs\pre_install_status.txt

# Snapshot of WU surface for later diff
Get-Service wuauserv,UsoSvc,DoSvc,WaaSMedicSvc,edgeupdate,edgeupdatem |
    Format-Table Name,Status,StartType -AutoSize | Out-File ..\logs\pre_services.txt

Get-ScheduledTask -TaskPath '\Microsoft\Windows\WindowsUpdate\','\Microsoft\Windows\UpdateOrchestrator\','\Microsoft\Windows\WaaSMedic\' -ErrorAction SilentlyContinue |
    Format-Table TaskPath,TaskName,State -AutoSize | Out-File ..\logs\pre_tasks.txt
```

**Expected baseline on a vanilla rig:**

| Item | Likely value |
|---|---|
| `wuauserv` Status | `Running` or `Stopped` (Windows toggles it on demand) |
| `wuauserv` StartType | `Manual` |
| `UsoSvc` Status | `Running` |
| `UsoSvc` StartType | `Manual` (it's "Automatic (Trigger Start)" in Services UI) |
| `WaaSMedicSvc` StartType | `Manual` |
| WU scheduled tasks | All `Ready` |
| `NoAutoUpdate` policy | `(key absent)` |

If your baseline already shows everything `Disabled`, someone locked this
rig before. Stop and investigate before re-running anything.

---

## Tier 1 — Install (the actual test)

```powershell
.\windows_lockdown.ps1 -Install
```

Single line. The script:
1. Verifies it's elevated (errors out if not).
2. Registers `Watchdog\WindowsLockdown_AtStartup` (SYSTEM, at-startup).
3. Registers `Watchdog\WindowsLockdown_Daily` (SYSTEM, 06:17 daily).
4. Calls `Invoke-WindowsLockdown` once immediately so the rig is locked
   right now, not next reboot.

**Watch the console scroll.** Every action logs. If you see any `FAILED` or
`WARN` lines, copy them — that's a signal Microsoft has changed something
or our script is missing a permission.

The full log is also at `..\logs\windows_lockdown.log` for later review.

---

## Tier 2 — Verify the install stuck (no reboot yet)

```powershell
.\windows_lockdown.ps1 -Status | Tee-Object -FilePath ..\logs\post_install_status.txt
```

**Pass criteria** — every line should now read:

| Item | Required value |
|---|---|
| `wuauserv` status | `Stopped` |
| `wuauserv` startValue | `4` |
| `UsoSvc` status | `Stopped` |
| `UsoSvc` startValue | `4` |
| `DoSvc` startValue | `4` |
| `WaaSMedicSvc` startValue | `4` |
| `edgeupdate` startValue | `4` |
| `edgeupdatem` startValue | `4` |
| WU/UpdateOrchestrator/WaaSMedic tasks | All `Disabled` |
| `policy NoAutoUpdate` | `1` |

Confirm our scheduled tasks were registered:

```powershell
Get-ScheduledTask -TaskPath '\Watchdog\' | Format-Table TaskName,State,@{N='Trigger';E={$_.Triggers[0].CimClass.CimClassName}}
```

Expected:

```
TaskName                     State Trigger
--------                     ----- -------
WindowsLockdown_AtStartup    Ready MSFT_TaskBootTrigger
WindowsLockdown_Daily        Ready MSFT_TaskDailyTrigger
```

Spot-check the policy registry:

```powershell
Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' | Select NoAutoUpdate,AUOptions
Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\EdgeUpdate' | Select UpdateDefault,AutoUpdateCheckPeriodMinutes
Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent' | Select DisableWindowsConsumerFeatures,DisableWindowsSpotlightFeatures
```

All three should return their values, no `PropertyNotFoundException`.

**If any line fails this tier:** read the most recent
`..\logs\windows_lockdown.log` block — search for `FAILED` and `WARN`.
Send me the failing lines verbatim.

---

## Tier 3 — Reboot persistence

The whole point: settings survive a restart and the at-startup task fires.

```powershell
Restart-Computer -Force
```

After the box comes back, log in and from elevated PowerShell:

```powershell
cd <deploy>\Boot

# Did our at-startup task run?
Get-ScheduledTask -TaskPath '\Watchdog\' WindowsLockdown_AtStartup |
    Get-ScheduledTaskInfo | Format-List TaskName,LastRunTime,LastTaskResult,NextRunTime
```

`LastRunTime` should be within a minute or two of boot.
`LastTaskResult` should be `0` (success).

```powershell
# Did the lockdown survive the reboot?
.\windows_lockdown.ps1 -Status
```

Same pass criteria as Tier 2 — all services `Stopped`/`startValue=4`, all
tasks `Disabled`. If anything resurrected, that's the most important
signal we get out of this whole test: it tells us *which heal-vector*
slipped through.

Tail the log for the boot-time run:

```powershell
Get-Content ..\logs\windows_lockdown.log -Tail 50
```

Look for a fresh `======== Invoke-WindowsLockdown begin ========` block
dated post-reboot, with no `FAILED` lines.

---

## Tier 4 — Adversarial reapply (does the daily task heal?)

This is the real durability test. We deliberately break the lockdown,
then run the script the way the daily task will, and confirm everything
gets put back.

```powershell
# Manually re-enable everything we just disabled
Set-Service wuauserv -StartupType Manual
Start-Service wuauserv -ErrorAction SilentlyContinue
Set-Service UsoSvc -StartupType Manual
Get-ScheduledTask -TaskPath '\Microsoft\Windows\WindowsUpdate\' | Enable-ScheduledTask
Remove-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Name 'NoAutoUpdate' -ErrorAction SilentlyContinue

# Confirm we successfully broke things
.\windows_lockdown.ps1 -Status
# Expected: wuauserv Running, NoAutoUpdate (key absent), tasks Ready
```

Now simulate the 06:17 daily run firing:

```powershell
Start-ScheduledTask -TaskPath '\Watchdog\' -TaskName 'WindowsLockdown_Daily'
Start-Sleep -Seconds 30
.\windows_lockdown.ps1 -Status
```

**Pass criteria:** everything we just broke is back to locked-down. If
*anything* stays broken, the daily reapply is missing a code path.

---

## Tier 5 — Real-world: try to actually update

Settings UI test (manual):

1. Open `Settings -> Windows Update`. Click **Check for updates**.
2. Expected: the page errors with **"Some settings are managed by your
   organization"** and the check fails. No updates listed.

Programmatic test:

```powershell
$session = New-Object -ComObject 'Microsoft.Update.Session'
$searcher = $session.CreateUpdateSearcher()
try {
    $searcher.Search('IsInstalled=0') | Out-Null
    'FAIL: WU API still working'
} catch {
    'PASS: WU API blocked - ' + $_.Exception.Message
}
```

Should print `PASS: WU API blocked` (commonly with HRESULT `0x8024402c`,
`0x80244022`, or "The service cannot be started").

Edge auto-update test:

```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -like 'MicrosoftEdgeUpdate*' } |
    Format-Table TaskName,State -AutoSize
Get-Service edgeupdate,edgeupdatem | Format-Table Name,Status,StartType
```

Tasks should be `Disabled`, services `Stopped`/`Disabled`.

---

## Tier 6 — One pilot rig, one week

Only after Tiers 0-5 all pass on a VM. Pick the least-critical rig.

**Daily check-in for 7 days:**

```powershell
.\windows_lockdown.ps1 -Status
Get-Content ..\logs\windows_lockdown.log -Tail 30
```

**Watch for in the log:**
- A new `Invoke-WindowsLockdown begin` block every 24h around 06:17.
- Zero `FAILED` lines, ideally zero `WARN`.
- If the reg-set / svc-disable lines are *re-applying* the same value
  every day, that's expected and fine. If they change values back, we'd
  see a transition logged — that's also fine.

**Watch for on the rig:**
- Did Watchdog get focus-stolen by any popup in those 7 days? If yes,
  what was it? Take a screenshot if you can.
- Did Steam start failing logins? (Could indicate a clock-sync or service
  we shouldn't have killed — should not happen, w32time is on the
  do-not-touch list.)
- Did anything else break? (CS2 launches, RDP, ExpressVPN, etc.)

After 7 clean days, fleet rollout.

---

## What to send me when reporting

The three log files plus a one-liner:

```powershell
# These already exist after any -Apply / -Install run. No need to re-run, just collect:
Get-Content ..\logs\windows_lockdown_status.txt   # current verdict + per-check
Get-Content ..\logs\windows_lockdown_history.log  # one line per run, all of them
Get-Content ..\logs\windows_lockdown.log -Tail 200  # full chatter from the most recent run

# Plus our scheduled task health
Get-ScheduledTask -TaskPath '\Watchdog\' | Get-ScheduledTaskInfo | Format-List TaskName,LastRunTime,LastTaskResult,NextRunTime
```

Then the contents of those plus:
- Which tier you're on
- Which step failed (if any) — exact line number from the runbook
- Whether you see any unexpected popups on the rig

---

## Rollback (manual, if a rig misbehaves)

`-Uninstall` removes our scheduled tasks, but does **not** undo the
service/registry changes. Manual undo:

```powershell
# 1. Stop the daily reapply first, otherwise undo gets re-undone at 06:17
.\windows_lockdown.ps1 -Uninstall

# 2. Re-enable services
foreach ($s in 'wuauserv','UsoSvc','DoSvc','WaaSMedicSvc','edgeupdate','edgeupdatem') {
    $svc = Get-Service -Name $s -ErrorAction SilentlyContinue
    if ($svc) {
        # Restore to "Manual" — the original default for these services
        Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\$s" -Name 'Start' -Value 3
    }
}

# 3. Re-enable scheduled tasks
foreach ($p in '\Microsoft\Windows\WindowsUpdate\','\Microsoft\Windows\UpdateOrchestrator\','\Microsoft\Windows\WaaSMedic\','\Microsoft\Windows\InstallService\') {
    Get-ScheduledTask -TaskPath $p -ErrorAction SilentlyContinue | Enable-ScheduledTask
}

# 4. Drop the policy keys (this is the cleanest signal Windows looks at)
Remove-Item 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item 'HKLM:\SOFTWARE\Policies\Microsoft\EdgeUpdate' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore' -Recurse -Force -ErrorAction SilentlyContinue

# 5. Reboot to be safe
Restart-Computer -Force
```

After reboot, `Get-Service wuauserv` should be back to `Manual` and
`Settings -> Windows Update -> Check for updates` should work normally.

---

## Failure interpretation cheat-sheet

| Symptom | Likely cause | Fix |
|---|---|---|
| `-Install` errors with "elevated context required" | Not running as admin | Right-click PowerShell -> Run as administrator |
| `task disable FAILED` for a `WaaSMedic` task | Microsoft locked the task ACL on this build | Need `schtasks.exe /Change /DISABLE` with SYSTEM token, or accept that task and rely on `Start=4` instead |
| `wuauserv` re-enabled itself between Tier 2 and Tier 3 | UsoSvc trigger or Modules Installer servicing event | Confirm `UsoSvc` startValue is also `4`; if yes, escalate to `sc.exe sdset` denying SYSTEM |
| Settings UI doesn't show "managed by your org" | `NoAutoUpdate` reg write didn't land or got rolled back | Check `..\logs\windows_lockdown.log` for the AU reg-set line |
| Steam logins start failing | Something killed `w32time` (shouldn't happen with this script) | `Set-Service w32time -StartupType Automatic; Start-Service w32time` |
| Watchdog logs lots of "Could not focus window" again | A popup we didn't suppress | Note what the popup *is* and which app owns it; we add it to the next pass |

---

## Future work (not in this script)

- Per-user logon task to suppress Spotlight / ContentDelivery on the
  *currently-logged-in* farming user (script today only sets `.DEFAULT`,
  affecting new profiles).
- `-Rollback` switch to the script (currently rollback is manual).
- `sc.exe sdset` escalation for protected-service holdouts.
