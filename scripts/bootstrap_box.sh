#!/usr/bin/env bash
# bootstrap_box.sh — one-time per-box install/upgrade of the Watchdog stack.
#
# Usage:
#   ./scripts/bootstrap_box.sh <user@host> <password-file> <dist-dir> <farm-token>
#
#   <user@host>      e.g. Farmer7@192.168.1.132 (main-session admin user)
#   <password-file>  file containing ONLY the ssh password (chmod 600)
#   <dist-dir>       local dir with the release exes, e.g. after:
#                      gh release download -R AdxamAxatov/Watchdog -p '*.exe' -D dist
#   <farm-token>     per-box X-Farm-Token written into farm_agent_config.yaml
#                    (only if the box has no config yet) and used for the
#                    acceptance check.
#
# What it does (idempotent; every step echoes a receipt; aborts loudly):
#   1. stop WindowChecker/FarmAgent tasks + processes
#   2. backup existing exes (<exe>.exe.pre-bootstrap)
#   3. push new exes into their deploy folders (Documents\<App>\)
#   4. push config templates ONLY where the box has none (never overwrites
#      per-machine regions.yaml / tokens)
#   5. register FarmAgent (ONLOGON) + FarmAgentKeepAlive (10-min start-if-
#      not-running) Task Scheduler tasks
#   6. restart everything and curl /status as the acceptance gate
#
# Inventory loop example:
#   while read -r box; do ./scripts/bootstrap_box.sh "$box" .pw dist "$TOKEN"; done < farm_inventory
set -euo pipefail

BOX="${1:?user@host}"; PWFILE="${2:?password file}"; DIST="${3:?dist dir}"; TOKEN="${4:?farm token}"
HOST="${BOX#*@}"; RUSER="${BOX%@*}"
PORT=8765
DOCS="C:/Users/${RUSER}/Documents"

ssh_run() { sshpass -f "$PWFILE" ssh -o ConnectTimeout=10 "$BOX" "$@"; }
push()    { sshpass -f "$PWFILE" scp -q "$1" "${BOX}:$2"; }

need() { command -v "$1" >/dev/null || { echo "FATAL: $1 not installed"; exit 1; }; }
need sshpass; need scp; need curl

for exe in WindowChecker FarmAgent; do
  [ -f "$DIST/$exe.exe" ] || { echo "FATAL: $DIST/$exe.exe missing"; exit 1; }
done

echo "== [$BOX] 1/6 stopping tasks + processes"
ssh_run 'schtasks /End /TN "WindowsChecker" 2>NUL & schtasks /End /TN "FarmAgent" 2>NUL & taskkill /F /IM WindowChecker.exe 2>NUL & taskkill /F /IM FarmAgent.exe 2>NUL & echo stopped' \
  | tail -1

echo "== [$BOX] 2/6 staging + backup"
ssh_run "cmd /c if not exist \"${DOCS//\//\\}\\staging\" mkdir \"${DOCS//\//\\}\\staging\" & echo staging-ok" | tail -1
for app in WindowChecker FarmAgent; do
  ssh_run "cmd /c if exist \"${DOCS//\//\\}\\${app}\\${app}.exe\" copy /Y \"${DOCS//\//\\}\\${app}\\${app}.exe\" \"${DOCS//\//\\}\\${app}\\${app}.exe.pre-bootstrap\" >NUL & echo backup-${app}-ok" | tail -1
done

echo "== [$BOX] 3/6 pushing exes"
for app in WindowChecker FarmAgent; do
  push "$DIST/$app.exe" "$DOCS/staging/$app.exe"
  ssh_run "cmd /c if not exist \"${DOCS//\//\\}\\${app}\" mkdir \"${DOCS//\//\\}\\${app}\" & move /Y \"${DOCS//\//\\}\\staging\\${app}.exe\" \"${DOCS//\//\\}\\${app}\\${app}.exe\" >NUL & echo installed-${app}" | tail -1
done

echo "== [$BOX] 4/6 config templates (only where missing)"
for app in WindowChecker FarmAgent; do
  ssh_run "cmd /c if not exist \"${DOCS//\//\\}\\${app}\\config\" mkdir \"${DOCS//\//\\}\\${app}\\config\" & echo cfgdir-${app}-ok" | tail -1
done
# FarmAgent configs (write only if absent; token substituted locally first)
TMPCFG="$(mktemp)"
sed "s/^token: \"\"/token: \"${TOKEN}\"/" config/farm_agent_config.yaml > "$TMPCFG"
if ssh_run "cmd /c if exist \"${DOCS//\//\\}\\FarmAgent\\config\\farm_agent_config.yaml\" (echo EXISTS) else (echo ABSENT)" | grep -q ABSENT; then
  push "$TMPCFG" "$DOCS/FarmAgent/config/farm_agent_config.yaml"
  push "config/farm_agent_update_config.yaml" "$DOCS/FarmAgent/config/farm_agent_update_config.yaml"
  echo "   pushed farm_agent configs (token set)"
else
  echo "   farm_agent_config.yaml already on box — left untouched"
fi
rm -f "$TMPCFG"

echo "== [$BOX] 5/6 registering FarmAgent tasks"
FA_EXE="${DOCS//\//\\}\\FarmAgent\\FarmAgent.exe"
ssh_run "schtasks /Create /F /SC ONLOGON /RL HIGHEST /TN FarmAgent /TR \"${FA_EXE}\" && echo task-FarmAgent-ok" | tail -1
ssh_run "schtasks /Create /F /SC MINUTE /MO 10 /RL HIGHEST /TN FarmAgentKeepAlive /TR \"${FA_EXE}\" && echo task-KeepAlive-ok" | tail -1

echo "== [$BOX] 6/6 restart + acceptance"
ssh_run 'schtasks /Run /TN "WindowsChecker" & schtasks /Run /TN "FarmAgent" & echo started' | tail -1
sleep 8
STATUS="$(curl -s -m 10 -H "X-Farm-Token: ${TOKEN}" "http://${HOST}:${PORT}/status" || true)"
if echo "$STATUS" | grep -q '"box"'; then
  echo "== [$BOX] ACCEPTED — /status: $(echo "$STATUS" | head -c 200)"
else
  echo "== [$BOX] FAILED acceptance — /status gave: '${STATUS:-<empty>}'"
  echo "   (check firewall for port ${PORT}, task registration, agent log)"
  exit 1
fi
