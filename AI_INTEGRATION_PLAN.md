# Plan — AI-driven panel decisions for Watchdog

## Context

The current `Watchdog.exe` decides what to do with the FSM panel via brittle rule pipelines: Tesseract OCRs the logbox, regex `ENTRY_RE` parses `HH:MM | message`, and a handful of hard-coded thresholds (`general_timeout_minutes`, "cannot add" substring match, `latest_msg_is_warm`, `is_launch_in_progress`'s 10-min window) decide whether to click the recovery button, kill CS2, restart explorer, or run first-run clicks. Each of these rules has been a source of incidents — OCR misreads, edge-case messages the regex doesn't match, or new panel states the rules weren't written for. The user wants AI judgment to replace these interpretive decisions while leaving cheap operational plumbing (heartbeat, auto-update, window-find, normalization, CS2 process count) on the existing 3-min loop.

Approved choices:
- **Auth**: Claude Pro/Max via Claude Code SDK headless on each PC (uses bundled subscription quota, not per-token API)
- **Architecture**: Hybrid — AI judges interpretive checks every 15 min; existing rules remain as offline fallback only
- **Action allowlist**: all 4 — click recovery, kill+restart CS2, run first-run, restart explorer
- **Cadence**: 15 min (within the user-stated 10-20 min range)

## Architecture

```
3-min main loop (unchanged):
   heartbeat write → auto-update check → find/launch panel → normalize window
   → first-run pending retry → CS2 instance count (every 5 min)
   → periodic explorer restart (every 30 min) → sleep(poll)

NEW periodic consult (every 15 min):
   capture full panel screenshot
   tail last ~30 lines of current logs/watchdog_*.log
   call Claude Code SDK with structured prompt + image
   parse JSON response → execute action → record decision

Fallback rule:
   if AI has been unreachable continuously > 30 min,
   run the existing rule-based interpretive checks for that loop
   (recovery trigger, "cannot add" → restart_explorer, etc.)
```

### Why hybrid (not full replacement)

Operational steps don't benefit from AI and shouldn't wait 15 min: panel-not-found needs immediate launch, heartbeat must fire every loop, CS2 process count is a process-table read. Only the *interpretive* decisions get AI'd.

### Why "rules dormant" not "rules + AI both run"

Running both would risk double-actions (rule clicks recovery, AI also clicks recovery 30 seconds later). Cleaner to make rules dormant whenever AI is healthy and only wake them after a sustained AI outage.

## Decisions AI replaces

From the existing `watchdog.py` decision map:
1. `find_latest_entry` (OCR parsing) — AI looks at panel directly, no OCR needed
2. `latest_msg_is_warm` (warm-up regex) — AI judges from screenshot
3. `is_launch_in_progress` (Launching age < 10 min) — AI judges
4. Recovery trigger (`minutes_ago >= general_timeout`) — AI decides
5. "Cannot add" detection (substring in latest_msg) — AI sees error
6. First-run-needed detection — AI sees first-run screen

Operational checks that stay (rule-based, every loop):
- Window exists / launch panel
- CS2 instance count (process-table, not screenshot-interpretive)
- Heartbeat write
- Auto-update check
- Periodic explorer restart
- Window normalization

## Components to add

### `src/ai_decider.py` (new file)

Single module that wraps the Claude Code SDK call.

```python
@dataclass
class ActionDecision:
    action: Literal['noop', 'click_recovery', 'kill_cs2',
                    'first_run', 'restart_explorer', 'launch_panel', 'unknown']
    reason: str

def consult_ai(hwnd: int, regions: dict, log) -> ActionDecision | None:
    # 1. Capture full panel client area via existing capture_window_region_api
    # 2. Save PNG to %TEMP%\watchdog_ai\panel_<USERNAME>.png
    # 3. Read tail of newest logs/watchdog_*.log (last ~30 lines)
    # 4. Call claude_agent_sdk.query(...) with system prompt + image + log tail
    # 5. Parse JSON action from response (strict json.loads + retry one-shot)
    # 6. Return ActionDecision, or None on any failure
```

System prompt sketch (tight, no chain-of-thought):
> You watch an FSM panel that controls 4 CS2 instances for game farming. Look at the panel screenshot and the recent log tail. Output ONLY a JSON object: `{"action": <one_of_allowed>, "reason": "<short>"}`. Allowed actions: noop, click_recovery, kill_cs2, first_run, restart_explorer, launch_panel, unknown. Use 'unknown' if you see a state you don't recognize.

The SDK uses subscription auth that's already on the machine (the user's logged-in Claude Code). No API key in code.

### Action dispatcher (in `watchdog.py`)

```python
def execute_ai_action(decision, hwnd, regions, log) -> bool:
    """Returns True if any action was taken."""
    if decision.action in ('noop', 'unknown'):
        if decision.action == 'unknown':
            log.warning("AI flagged unknown panel state: %s", decision.reason)
        return False
    if decision.action == 'click_recovery':
        # Reuse existing recovery action (currently inside trigger_recovery_action)
        ...
    elif decision.action == 'kill_cs2':
        # Reuse the kill+click branch from check_cs2_instance_count
        ...
    elif decision.action == 'first_run':
        run_panel_first_run_if_needed(hwnd, regions, log=log, force=True)
    elif decision.action == 'restart_explorer':
        restart_explorer(log=log)
    elif decision.action == 'launch_panel':
        # Reuse the launch block currently inside the if-not-hwnd branch
        ...
    return True
```

Where possible, refactor existing logic into reusable helpers so AI dispatcher and rule fallback share the same action implementations — no duplication.

### Main-loop integration (in `watchdog.py`)

New state vars near other timers (around `last_update_check_ts`):
```python
last_ai_consult_ts = 0.0
ai_first_consult_stagger = random.randint(0, 300)  # 0-5 min random offset
ai_unavailable_since: float | None = None
```

New block in the `while True:` body, placed AFTER the operational stuff but BEFORE the rule-based interpretive checks:
```python
ai_consult_interval = ai_cfg.get("consult_interval_minutes", 15) * 60
if time.time() - last_ai_consult_ts >= ai_consult_interval:
    decision = consult_ai(hwnd, regions, log)
    if decision is not None:
        log.info("AI decision: %s — %s", decision.action, decision.reason)
        execute_ai_action(decision, hwnd, regions, log)
        ai_unavailable_since = None
    else:
        if ai_unavailable_since is None:
            ai_unavailable_since = time.time()
            log.warning("AI consult failed — starting fallback timer")
    last_ai_consult_ts = time.time()
```

Then gate the existing interpretive rule checks behind the fallback timer:
```python
ai_fallback_after = ai_cfg.get("fallback_after_minutes", 30) * 60
ai_in_fallback = (
    ai_unavailable_since is not None
    and (time.time() - ai_unavailable_since) > ai_fallback_after
)
if ai_in_fallback:
    log.warning("AI dormant > %d min, running rule-based fallback", ai_fallback_after // 60)
    # existing OCR → find_latest_entry → recovery / cannot-add / first-run logic
    ...
```

### Config (in `config/app.yaml`)

```yaml
ai:
  enabled: true
  consult_interval_minutes: 15
  fallback_after_minutes: 30
  model: claude-opus-4-7   # or claude-sonnet-4-6 for cheaper
```

If `enabled: false`, the SDK is never invoked and the loop runs purely on rules. Lets you turn AI off without redeploying.

## Critical files to modify

| File | Change |
|---|---|
| `src/watchdog.py` | Add main-loop AI consult block + dispatcher; gate existing interpretive checks behind `ai_in_fallback` |
| `src/ai_decider.py` | **New** — Claude SDK wrapper |
| `requirements.txt` | Add `claude-agent-sdk` |
| `config/app.yaml` | Add `ai:` block |

Existing utilities to reuse (don't duplicate):
- `capture_window_region_api(hwnd, x, y, w, h)` from `src/vision.py` — fast unfocused screenshot
- `client_size(hwnd)` from `src/winops.py` — for full-window region
- `force_foreground(hwnd, ...)` from `src/winops.py` — only if click execution needs focus
- `safe_click(...)` from `src/winops.py` — click execution
- `pct_to_screen_xy(...)` from `src/winops.py` — for `button_point_pct`, etc.
- `restart_explorer(log)` already in `watchdog.py`
- `run_panel_first_run_if_needed(hwnd, regions, log, force=True)` already in `watchdog.py`
- `count_cs2_instances()`, `kill_all_cs2()` if present in `watchdog.py`

## Deployment considerations

1. **Claude Code on each production PC** — must be installed and authenticated to the user's Pro/Max subscription, *per user account* if both RDP users will run AI-enabled Watchdog. Auth is per-user in `%APPDATA%`. Check whether one Claude Code login can serve both sessions or if both users need to log in separately.
2. **claude-agent-sdk in the bundled venv / PyInstaller output** — add to `requirements.txt` and verify it bundles cleanly with `--onefile`. May need a `hiddenimports=['claude_agent_sdk', ...]` in the spec, OR add `--collect-all claude_agent_sdk` to the build command. Test before mass deploy.
3. **Subscription quota** — 2 sessions × N PCs × 96 calls/day (15-min cadence) is the daily floor. With Sonnet-class images (~3-5K input tokens/check), this fits comfortably in Max's quota; with Opus it's tighter. Default to Sonnet 4.6 in config; switch to Opus for higher accuracy if needed.
4. **First-call stagger** — `ai_first_consult_stagger` random 0-300s offset on startup so the two RDP-session Watchdogs don't hit the SDK simultaneously, mirroring the existing auto-updater stagger.
5. **Network outage tolerance** — the 30-min fallback timer keeps the rig running on rules during transient outages without a flood of restart attempts.

## Verification

After implementation, on a dev machine:
1. `pip install -r requirements.txt` (will pull `claude-agent-sdk`)
2. `claude` CLI: confirm `claude --version` works and login is cached
3. `venv\Scripts\python src\watchdog.py` against a live panel
4. Watch console / `logs/watchdog_*.log` — should see one `AI decision: noop — ...` entry within 5 min of startup (after stagger), then one every 15 min
5. **Recovery test**: leave the panel idle past `general_timeout` minutes; verify AI returns `click_recovery` and the dispatcher fires the same `safe_click(button_point_pct)` the rules would have
6. **First-run test**: kill the panel, watchdog auto-launches it; the existing 3x first-run logic still runs at launch time. AI should later return `noop` once first-run has completed (logbox populated).
7. **Unknown-state test**: present an artificial unrecognized state (e.g. a notepad covering the panel partially) → AI should return `unknown` and dispatcher should log it without taking action.
8. **Fallback test**: block outbound HTTPS to anthropic.com / claude.ai (firewall rule); after 30 min the loop should log "AI dormant > 30 min, running rule-based fallback" and the existing rule logic should resume.
9. **Re-auth test**: lift the firewall rule; on the next 15-min consult the SDK call should succeed and `ai_unavailable_since` should reset to None (rule fallback goes dormant again).

Once it's solid on the dev machine, build with the existing PyInstaller commands and test on one production PC (one session) before rolling out to both sessions and other PCs.

---

## Pending additions (TODO before implementation)

The user has more features to add to this plan before execution begins. Drop them below as they come in:

- _(awaiting user input)_

---

## Deferred — CS2 file validator merge (origin/twin)

A CS2 file validator was implemented on the `twin` branch by the user (commit `0566bc3 Add daily CS2 file validation`). It adds `src/steps/cs2_validate.py` (~106 lines) and a 12-line wiring change in `src/watchdog.py`.

**Decision:** merge deferred — pick this back up later.

**Confirmed design intent:** both RDP users share the main user's Steam install, so a single validation pass covers both sessions. The shared marker file (`logs/cs2_validate_last_run.txt`, no per-user suffix) is intentional — first session to validate writes the marker, the other session sees it fresh and skips.

**Known issues to resolve before merging to `main`:**

1. **Timing arithmetic in `watchdog.py` wiring is off-by-300s.**
   `last_cs2_check_ts = time.time() + (15 * 60) - 300` defers the next CS2 instance check by 10 min, but the comment says 15. Either drop the `- 300` to defer the full 15 min, or rewrite the comment to reflect the actual 10-min defer.

2. **Tiny race on simultaneous trigger from both sessions.**
   Both Watchdogs call `cs2_validate_run()` every loop. There's a millisecond-wide window where both could see "no marker" and fire `start steam://validate/730` together. Steam dedupes internally so it's harmless, but the validator should **write the marker BEFORE launching Steam** (not after) to close the race tidily. One-line reorder in `cs2_validate.py`.

3. **Already mitigated by today's `cs2_youngest_age_seconds` change** (no fix needed):
   When Steam starts validating it closes CS2. The new process-age skip in `check_cs2_instance_count` correctly notices "no cs2.exe in this session, youngest_age=None" → next loop tries to relaunch via the panel once validation finishes. No double-kill risk.

**Polish items (defer further, harmless):**
- `logging.basicConfig()` at module level in `cs2_validate.py` — only matters if imported from a context with no logger; benign in practice.
- 24h interval is hardcoded — fine for v1, can be moved to `config/app.yaml` later.
- The redundant `sys.path` dance vs. `from utils import exe_dir` — works but ugly.

**To resume:**
1. `git fetch origin twin && git checkout origin/twin -- src/steps/cs2_validate.py src/watchdog.py`
2. Apply fixes #1 (timing) and #2 (marker-before-launch order)
3. Merge to `main`
