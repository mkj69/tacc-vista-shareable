#!/usr/bin/env bash
set -euo pipefail

lib_dir="$HOME/.local/lib/tacc-vista"
# shellcheck source=/dev/null
source "$lib_dir/common.sh"
load_config "$HOME/.config/tacc-vista/config"
require_config_vars LOGIN_NODE_PATTERN

dashboard="${VISTA_DASHBOARD_SOURCE:-$lib_dir/vista_job_dashboard.py}"
bind="${VISTA_DASHBOARD_BIND:-127.0.0.1}"
port="${VISTA_DASHBOARD_PORT:-8765}"
interval="${VISTA_DASHBOARD_INTERVAL:-5}"
host_short="$(hostname -s)"
state_dir="$HOME/.cache/vista-dashboard"
pid_file="$state_dir/${host_short}-${port}.pid"
log_file="$state_dir/${host_short}-${port}.log"

case "$host_short" in
    $LOGIN_NODE_PATTERN)
        ;;
    *)
        printf 'Run the dashboard on a Vista login node so GPU sampling can launch Slurm steps.\n' >&2
        exit 1
        ;;
esac

if [[ ! -f "$dashboard" ]]; then
    printf 'Dashboard source not found: %s\n' "$dashboard" >&2
    exit 1
fi

mkdir -p "$state_dir"

if [[ -f "$pid_file" ]]; then
    old_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
        old_command="$(ps -p "$old_pid" -o args= 2>/dev/null || true)"
        if [[ "$old_command" == *"$dashboard"* && "$old_command" == *"--port $port"* ]]; then
            kill "$old_pid"
            for _ in {1..20}; do
                kill -0 "$old_pid" 2>/dev/null || break
                sleep 0.1
            done
        fi
    fi
    rm -f "$pid_file"
fi

nohup python3 "$dashboard" \
    --bind "$bind" \
    --port "$port" \
    --user "${VISTA_DASHBOARD_USER:-$USER}" \
    --interval "$interval" \
    >"$log_file" 2>&1 </dev/null &
dashboard_pid=$!
printf '%s\n' "$dashboard_pid" >"$pid_file"

for _ in {1..40}; do
    if ! kill -0 "$dashboard_pid" 2>/dev/null; then
        printf 'Dashboard exited during startup. Log: %s\n' "$log_file" >&2
        tail -n 30 "$log_file" >&2 || true
        exit 1
    fi
    if python3 - "$bind" "$port" <<'PY' >/dev/null 2>&1
import socket
import sys

with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=0.25):
    pass
PY
    then
        printf 'Dashboard ready on %s at %s:%s (PID %s).\n' \
            "$host_short" "$bind" "$port" "$dashboard_pid"
        printf 'Log: %s\n' "$log_file"
        exit 0
    fi
    sleep 0.25
done

printf 'Dashboard did not become ready. Log: %s\n' "$log_file" >&2
tail -n 30 "$log_file" >&2 || true
exit 1
