#!/bin/bash
set -euo pipefail

lib_dir="$HOME/.local/lib/tacc-vista"
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/tacc-vista/codex-windows"

# shellcheck source=/dev/null
source "$lib_dir/common.sh"
load_config "$HOME/.config/tacc-vista/config"
require_config_vars REMOTE_PROJECT_DIR
cd "$REMOTE_PROJECT_DIR"
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

window="${1:?managed window name is required}"
mode="${2:-resume}"
resume_ref="${3:-}"
window_dir="$state_dir/windows/$window"
mkdir -p "$window_dir"
: >"$window_dir/active"
printf '%s\n' "$(hostname -s)" >"$window_dir/last-node"
chmod 700 "$state_dir" "$state_dir/windows" "$window_dir" 2>/dev/null || true
chmod 600 "$window_dir/active" "$window_dir/last-node" 2>/dev/null || true

record_session_ref() {
    local runner_pid="$1" pid child fd target session_id tmp
    while kill -0 "$runner_pid" 2>/dev/null; do
        for pid in $(pgrep -P "$runner_pid" 2>/dev/null || true); do
            for child in "$pid" $(pgrep -P "$pid" 2>/dev/null || true); do
                for fd in "/proc/$child/fd"/*; do
                    [[ -L "$fd" ]] || continue
                    target="$(readlink "$fd" 2>/dev/null || true)"
                    case "$target" in
                        "$CODEX_HOME"/sessions/*.jsonl|"$CODEX_HOME"/sessions/*/*.jsonl|"$CODEX_HOME"/sessions/*/*/*.jsonl|"$CODEX_HOME"/sessions/*/*/*/*.jsonl)
                            session_id="$(head -n 1 "$target" 2>/dev/null | python3 -c 'import json,sys; p=json.load(sys.stdin).get("payload",{}); print(p.get("id") or p.get("session_id") or "")' 2>/dev/null || true)"
                            if [[ -n "$session_id" ]]; then
                                tmp="$window_dir/session-ref.tmp.$$"
                                printf '%s\n' "$session_id" >"$tmp"
                                chmod 600 "$tmp" 2>/dev/null || true
                                mv "$tmp" "$window_dir/session-ref"
                                return 0
                            fi
                            ;;
                    esac
                done
            done
        done
        sleep 1
    done
}

has_sessions() {
    [[ -d "$CODEX_HOME/sessions" ]] && find "$CODEX_HOME/sessions" -type f -name '*.jsonl' -print -quit | grep -q .
}

while true; do
    record_session_ref "$$" &
    monitor_pid=$!
    set +e
    if [[ "$mode" == new ]] || ! has_sessions; then
        codex
    elif [[ -n "$resume_ref" ]]; then
        codex resume "$resume_ref"
    else
        codex resume --all
    fi
    status=$?
    set -e
    kill "$monitor_pid" 2>/dev/null || true
    wait "$monitor_pid" 2>/dev/null || true

    if [[ "$status" -eq 0 || "$status" -eq 130 ]]; then
        rm -f "$window_dir/active"
        printf '\nCodex window %s was closed normally and will not be restored on the next node.\n' "$window"
        exit 0
    fi

    printf '\nCodex exited unexpectedly with status %d. Window %s remains marked for recovery.\n' "$status" "$window"
    printf '%s' '[r] retry/resume, [n] start new, [q] close and stop restoring: '
    if ! read -r action; then
        exit "$status"
    fi
    case "$action" in
        r|R|'') mode=resume; [[ ! -f "$window_dir/session-ref" ]] || resume_ref="$(head -n 1 "$window_dir/session-ref")" ;;
        n|N) mode=new; resume_ref=; rm -f "$window_dir/session-ref" ;;
        q|Q) rm -f "$window_dir/active"; exit 0 ;;
        *) printf '%s\n' 'Unknown choice; retrying the saved session.'; mode=resume ;;
    esac
done
