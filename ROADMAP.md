# Watchdog — Roadmap

Watchdog is the CS2 drop-farm automation suite (12 PCs × 2 RDP sessions): per-session `Watchdog.exe`, per-box `WindowChecker.exe` (RDP reconnect cycle), one-shot `Boot.exe`, plus DropStats/MemReductLooped. Current built state: manual releases replaced by CI auto-release (PR #2, pending merge); WindowChecker recovery logic has confirmed freeze-class bugs (host-67 autopsy 2026-07-06). Detailed design: `docs/superpowers/specs/2026-07-07-farm-self-healing-design.md`; implementation plan: `docs/superpowers/plans/2026-07-07-farm-self-healing.md`.

This ROADMAP is the single source of truth for what to build next.

---

## How to read this

- **Phases are ordered by value/effort**, with cross-phase prerequisites called out.
- **Effort** is S (≤½ day), M (1–2 days), L (3–5 days), XL (>1 week).
- Confirmed bugs (if any) are fixed before new feature phases.

---

## Confirmed bugs to fix first (hotlist)

| # | Sev | Bug | Where (file:line) | Effort | Fixed in |
|---|-----|-----|-------------------|--------|----------|
| 1 | critical | reposition deadlocks on hung window (sync SetWindowPos, no probe) — root cause of 7/4 + 7/6 outages | `src/steps/windows_focuser.py:283-293` | S | Phase 1 |
| 2 | critical | click-dispatch counted as confirmed disconnect → hung manager ghost-kills healthy sessions | `src/steps/windows_focuser.py:357,466-483` | M | Phase 1 |
| 3 | high | hwnd-pid kill can target dwm.exe via ghost windows | `src/steps/windows_focuser.py:209-236` | S | Phase 1 |
| 4 | high | reconnect reopens ~1.5s after Disconnect → teardown-arbitration race births frozen renderers | `src/steps/windows_focuser.py:302-374` | S | Phase 1 |
| 5 | high | health_check .bat loop zombies silently (observed twice) | deployed `health_check.bat` (not in repo) | M | Phase 2 (replaced by FarmAgent) |
| 6 | medium | `restart_watchdog_for_titles` substring digit-collision | `src/steps/windows_focuser.py:97` | S | Phase 1 |
| 7 | medium | `find_rdp_windows` no exe filter + `[:2]` z-order truncation | `src/steps/windows_focuser.py:30-46,438` | S | Phase 1 |
| ~~8~~ | ~~critical~~ | ~~exes built without `requests` → updaters shipped dead~~ | ~~requirements.txt~~ | — | ✅ fixed, PR #2 `7d00b99` |

---

## Phase 1 — WindowChecker hardening (freeze-proof recovery)

**Goal.** A frozen renderer or hung Session Manager can no longer wedge, deadlock, or mislead the recovery cycle.

**Deliverables.**
- `src/recovery_rules.py` (+ `Test files/test_recovery_rules.py`) — pure decision rules
- `window_responsive` / `resolve_real_hwnd` / `process_image_of` probes in `src/winops.py`
- Hardened `src/steps/windows_focuser.py` (R1–R6) + single-instance mutex & interval floor in `src/window_checker_main.py`
- `logs/recovery_state.json` breadcrumbs

**Why now.** This bug class cost ~24h farm downtime in the last week alone (7/4: 14.5h, 7/6: 10.5h); everything else builds on a checker that can't kill itself.

**Scope.** Plan Tasks 1–4 (`docs/superpowers/plans/2026-07-07-farm-self-healing.md`): rules module → winops probes → R1+R6 (`windows_focuser.py:30,97,283,438`) → R2–R5 (`windows_focuser.py:209,302,466`).

**Findings + recommendation.** Host-67 live autopsy: WC log + heartbeat frozen at the reposition line mid-cycle, WerFault attached to a 0-CPU newborn wfreerdp, LSM "End session arbitration" 8s after the reopen click, 6 WER wfreerdp hangs/17 days at ~600 cycles/day. Recommendation: probe-before-touch + async flags + effect-confirmation (ADR-002) rather than trying to make wfreerdp never hang.

**Risks.** Win32 hung-window edge cases only visible live → Phase 4 drill is a hard gate. Ghost-window map-back API is undocumented → fall back to image-name allowlist (never-kill list protects dwm/explorer regardless).

**Definition of done.** Unit suites green on mac; `py_compile` clean; in the Phase 4 freeze drill the WC log continues writing through a forced renderer freeze and the frozen window is killed with `image=wfreerdp.exe` logged.

---

## Phase 2 — FarmAgent supervisor + HTTP control plane

**Goal.** Every box supervises its own recovery chain and exposes status/actions to Sherlock Homeless; a wedged WindowChecker is auto-restarted and, as last rung, the box reboots itself (rate-limited).

**Deliverables.**
- `src/farm_agent_core.py` (+ tests) — health evaluation, escalation ladder, token-authed API
- `src/farm_agent_main.py` — snapshot adapters, action executor, wiring
- `config/farm_agent_config.yaml`, `config/farm_agent_update_config.yaml`
- Task Scheduler pair: `FarmAgent` (ONLOGON) + `FarmAgentKeepAlive` (10-min start-if-not-running)

**Why now.** Both outages persisted only because nothing watched the watcher (health_check zombied twice); the API is the owner-requested remote view/tweak/relaunch surface.

**Scope.** Plan Tasks 5–7: core logic TDD → API TDD → adapters/config. Prerequisite: none at build time (parallel to Phase 1), but drills need Phase 1.

**Findings + recommendation.** Supervisor must be a separate stdlib-only process (ADR-001): the .bat loop pattern zombied, and any pywin32 window call could deadlock exactly like WC did. Silence-is-unhealthy polling (Sherlock treats `/status` timeout as the alert) removes outbound dependencies.

**Risks.** Auto-reboot on a false positive → 3-consecutive-loops threshold + 1-per-2h rate limit + forced-only override via API (ADR-003). Port 8765 exposure → LAN-only fleet + shared token header; token never committed.

**Definition of done.** `Test files/test_farm_agent_core.py` green (evaluation, ladder, rate limiter, API auth/routes); on host-67: suspending WC produces an automatic restart within 2 loops and `curl /status` (valid token) returns full JSON while a bad token gets 401.

---

## Phase 3 — Ship it: CI sixth asset + PR #3

**Goal.** FarmAgent and the hardened WindowChecker propagate to the fleet through the same push-to-release pipeline as every other exe.

**Deliverables.**
- `.github/workflows/release.yml` builds/publishes `FarmAgent.exe` (6 assets, per-asset SHA256)
- Updated `AGENTS.md` (FarmAgent docs, API, new regions.yaml keys)
- PR #3 (`feat/farm-self-healing` → main, stacked on PR #2)

**Why now.** Without the pipeline the fixes die on this laptop; PR #2 must merge first (prerequisite — it carries the workflow + the requests fix).

**Scope.** Plan Task 8 (`release.yml:49-53,102,137-141`); open PR #3 after Phases 1–2 gates.

**Findings + recommendation.** Same asset-name-contract approach proven in PR #2; keep FarmAgent versioned/updated identically to the other exes — one mechanism, no special cases.

**Risks.** First real windows-runner PyInstaller run may need hidden-import tweaks (workflow is Spec-only until run #1) — budget one iteration.

**Definition of done.** Green Actions run publishes 6 assets with 6 `SHA256 (<exe>):` note lines; PR #3 open with drill receipts attached.

---

## Phase 4 — Live drills on host-67 (GATE)

**Goal.** Prove, on the real box, that the 7/4 + 7/6 failure signatures can no longer reproduce.

**Deliverables.**
- Drill receipts (log excerpts + `/status` JSON) pasted into PR #3
- `scripts/bootstrap_box.sh` exercised once end-to-end (single box)

**Why now.** Win32 semantics are only provable live; gating fleet rollout on a staged freeze drill is the difference between "tested" and "hoped".

**Scope.** Plan Tasks 9–10: bootstrap script → deploy to host-67 → API drill, freeze drill (suspend wfreerdp), supervisor drill (suspend WC), reboot-rung drill (announced, opt-in).

**Findings + recommendation.** host-67 already has SSH + a known-good backup config; it's the natural canary (it produced both incidents).

**Risks.** Drill itself disturbs a producing box → run during a low-value window, healthy session untouched, rollback = restore `.bak` config + previous exes (kept by bootstrap backup step).

**Definition of done.** All four drills pass with receipts; specifically: WC log writes *through* a forced freeze (bug #1 signature dead), and a suspended WC is auto-restarted (bug #5 signature dead).

---

## Phase 5 — Fleet rollout (12 boxes)

**Goal.** All 12 boxes run the hardened stack + FarmAgent and answer `/status` to Sherlock.

**Deliverables.**
- Completed inventory (12 × `user@host` + per-box tokens) — stays OUTSIDE the repo
- 12/12 bootstrap runs with `/status` acceptance receipts
- Per-box `regions.yaml`: `focus_interval_minutes: 30`, settle keys added

**Why now.** The fix only counts when the whole farm has it; every un-migrated box still carries the dead-updater trap (must be escaped manually exactly once).

**Scope.** `scripts/bootstrap_box.sh` loop over inventory; per-box: exe swap (Watchdog/WindowChecker/Boot/DropStats/MemReductLooped/FarmAgent), config templates where absent, task registration, `/status` check.

**Findings + recommendation.** 12 boxes / same stack → scripted ssh loop (no fleet tooling needed at this scale); host-67's recovery procedure is the tested template.

**Risks.** Per-box drift (paths, task names, session users) → bootstrap treats every assumption as a checked receipt and aborts loudly per box rather than continuing blind.

**Definition of done.** `for box in inventory: curl /status` → 12 healthy JSON responses; Sherlock polling all 12; zero boxes on pre-PR#2 exes.

---

## Phase 6 — Sherlock Homeless integration (consumer side)

**Goal.** A box unhealthy >10 min (or unreachable) pings Sigma's Telegram with host + failing check, and manual relaunch is one bot command away.

**Deliverables.**
- Sherlock-side poller against `GET /status` (12 boxes) + alert rule (timeout OR any `healthy: false` for 2 consecutive polls)
- Bot commands mapping to `POST /action/*`

**Why now.** Last link of the chain — turns "found out after 9 hours" into "phone buzz in 10 minutes". Lives in the Farm-Telegram-Monitoring repo, not here; tracked for sequencing only.

**Scope.** Out of this repo. Prerequisite: Phase 5 (all boxes answering). API contract: `docs/superpowers/specs/2026-07-07-farm-self-healing-design.md` §2.

**Findings + recommendation.** Owner decision (2026-07-07): boxes stay autonomous; Sherlock is view/control only, polling model, silence-is-unhealthy.

**Risks.** Sherlock itself is a single watcher → its own uptime is owner-managed; boxes remain fully autonomous without it (agent still heals + reboots).

**Definition of done.** Staged unhealthy state on one box produces a Telegram alert < 10 min and a bot-triggered `restart-windowchecker` round-trips with a 200.

---

## Architecture decisions (ADRs)

### ADR-001 — Supervisor is a separate stdlib-only process, not part of WindowChecker
**Decision.** Box supervision + control API live in `FarmAgent.exe` (stdlib http.server + subprocess only; no pywin32).
**Context.** WC deadlocked on a hung window twice; the .bat health loop zombied twice. A supervisor sharing the patient's failure modes is not a supervisor.
**Consequences.** (+) survives everything it supervises; trivially unit-testable off-Windows; (−) one more exe/task/config to deploy and version.

### ADR-002 — Disconnect is confirmed by observed effect, never by click dispatch
**Decision.** A session counts as cycled only if the success dialog was seen OR the old window was destroyed within the settle window.
**Context.** Click-dispatch-as-proof let a hung Session Manager mark phantom disconnects "confirmed", ghost-killing healthy renderers.
**Consequences.** (+) hung manager degrades to a logged no-op instead of farm damage; (−) adds up to ~10s settle-poll per session per cycle.

### ADR-003 — Auto-reboot is the final rung, rate-limited
**Decision.** After 3 consecutive unhealthy supervision loops with failed software recovery, the agent reboots the box; max 1 reboot per 2h; API `force` override.
**Context.** Both real incidents were ultimately fixed by a human reboot, 9–14h late. Owner approved automation (2026-07-07).
**Consequences.** (+) guaranteed unattended recovery ceiling ~15 min; (−) ~10 min interruption when it fires; boot-loop risk capped by the rate limit.

### ADR-004 — Fixes propagate via CI release assets, not git-pull on boxes
**Decision.** Boxes never run from source; every push to main builds+publishes exes; deployed updaters poll `releases/latest` (PR #2).
**Context.** Manual build+upload flow rotted (dead updaters shipped for weeks unnoticed); boxes have no python/git toolchain.
**Consequences.** (+) one propagation mechanism, checksummed; (−) windows-runner build is a CI dependency; one-time manual escape needed for boxes with dead updaters.

---

## Effort / impact table

| Item | Phase | Effort | Impact | Notes |
|------|-------|--------|--------|-------|
| WC hardening R1–R6 | 1 | M | High | kills both observed outage signatures |
| FarmAgent core+API | 2 | M | High | replaces zombied health_check; owner API |
| CI 6th asset + PR #3 | 3 | S | High | prerequisite: PR #2 merged |
| Live drills (gate) | 4 | S | High | proves it on the incident box |
| Fleet rollout ×12 | 5 | M | High | one-time manual escape from dead updaters |
| Sherlock integration | 6 | S–M | Medium | other repo; boxes autonomous without it |
| FreeRDP upgrade | — | M | Medium | WISHLIST — reduces trigger rate further |
