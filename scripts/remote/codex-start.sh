#!/bin/bash
set -euo pipefail

lib_dir="$HOME/.local/lib/tacc-vista"
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/tacc-vista/codex-windows"
windows_dir="$state_dir/windows"

# shellcheck source=/dev/null
source "$lib_dir/common.sh"
load_config "$HOME/.config/tacc-vista/config"
require_config_vars REMOTE_PROJECT_DIR TMUX_SESSION_NAME LOGIN_NODE_PATTERN

usage() {
    cat <<'EOF'
Usage:
  ~/start.sh                              restore all active windows and attach one
  ~/start.sh WINDOW                       restore/attach one named window
  ~/start.sh WINDOW CODEX_SESSION         bind and resume an exact Codex session
  ~/start.sh --new WINDOW                 create a new managed Codex window
  ~/start.sh --close WINDOW               close it and exclude it from future restores
  ~/start.sh --restore-all                restore every active window in the background
  ~/start.sh --list                       list saved and running windows
EOF
}

case "$(hostname -s)" in
    $LOGIN_NODE_PATTERN)
        printf '%s\n' 'Run the recovery command only after connecting to a compute node.' >&2
        exit 1
        ;;
esac
if [[ ! -d "$REMOTE_PROJECT_DIR" ]]; then
    printf '%s\n' 'The configured remote project directory does not exist.' >&2
    exit 1
fi
if ! command -v codex >/dev/null 2>&1; then
    printf '%s\n' 'Required command is not available: codex' >&2
    exit 1
fi
if ! command -v tmux >/dev/null 2>&1 && ! command -v screen >/dev/null 2>&1; then
    printf '%s\n' 'Neither tmux nor screen is available on this node.' >&2
    exit 1
fi

mkdir -p "$windows_dir"
chmod 700 "$state_dir" "$windows_dir" 2>/dev/null || true

validate_window_name() {
    [[ "$1" =~ ^[A-Za-z0-9_-]+$ ]] || {
        printf '%s\n' 'WINDOW may contain only letters, numbers, underscores, and hyphens.' >&2
        exit 2
    }
}

window_dir() { printf '%s/%s' "$windows_dir" "$1"; }
mux_name() { printf '%s-%s' "$TMUX_SESSION_NAME" "$1"; }

session_exists() {
    local name
    name="$(mux_name "$1")"
    if command -v tmux >/dev/null 2>&1; then
        tmux has-session -t "$name" 2>/dev/null
    else
        screen -ls 2>/dev/null | grep -Eq "[0-9]+[.]${name}[[:space:]]"
    fi
}

create_window() {
    local window="$1" mode="$2" resume_ref="$3" name runner_command dir
    validate_window_name "$window"
    dir="$(window_dir "$window")"
    mkdir -p "$dir"
    : >"$dir/active"
    printf '%s\n' "$(hostname -s)" >"$dir/last-node"
    if [[ "$mode" == new ]]; then
        rm -f "$dir/session-ref"
    elif [[ -n "$resume_ref" ]]; then
        printf '%s\n' "$resume_ref" >"$dir/session-ref"
    elif [[ -f "$dir/session-ref" ]]; then
        resume_ref="$(head -n 1 "$dir/session-ref")"
    fi
    chmod 600 "$dir"/* 2>/dev/null || true

    session_exists "$window" && return 0
    name="$(mux_name "$window")"
    if command -v tmux >/dev/null 2>&1; then
        printf -v runner_command '%q ' "$lib_dir/run-codex.sh" "$window" "$mode" "$resume_ref"
        tmux new-session -d -s "$name" -c "$REMOTE_PROJECT_DIR" "$runner_command"
    else
        screen -dmS "$name" "$lib_dir/run-codex.sh" "$window" "$mode" "$resume_ref"
    fi
}

restore_all() {
    local marker window ref
    for marker in "$windows_dir"/*/active; do
        [[ -f "$marker" ]] || continue
        window="${marker%/active}"
        window="${window##*/}"
        ref=
        [[ ! -f "$(window_dir "$window")/session-ref" ]] || ref="$(head -n 1 "$(window_dir "$window")/session-ref")"
        create_window "$window" resume "$ref"
    done
    return 0
}

list_windows() {
    local dir window status ref
    printf '%-22s %-10s %s\n' WINDOW STATE CODEX_SESSION
    for dir in "$windows_dir"/*; do
        [[ -d "$dir" ]] || continue
        window="${dir##*/}"
        status=closed
        [[ ! -f "$dir/active" ]] || status=active
        if session_exists "$window"; then status=running; fi
        ref='-'
        [[ ! -f "$dir/session-ref" ]] || ref="$(head -n 1 "$dir/session-ref")"
        printf '%-22s %-10s %s\n' "$window" "$status" "$ref"
    done
}

close_window() {
    local window="$1" name dir
    validate_window_name "$window"
    name="$(mux_name "$window")"
    dir="$(window_dir "$window")"
    rm -f "$dir/active"
    if command -v tmux >/dev/null 2>&1; then
        tmux kill-session -t "$name" 2>/dev/null || true
    else
        screen -S "$name" -X quit 2>/dev/null || true
    fi
    printf 'Closed %s; it will not be restored on the next node.\n' "$window"
}

attach_window() {
    local name
    name="$(mux_name "$1")"
    if command -v tmux >/dev/null 2>&1; then
        exec tmux attach-session -t "$name"
    else
        exec screen -D -r "$name"
    fi
}

case "${1:-}" in
    --help|-h) usage; exit 0 ;;
    --list) list_windows; exit 0 ;;
    --restore-all) restore_all; list_windows; exit 0 ;;
    --close)
        [[ -n "${2:-}" ]] || { usage >&2; exit 2; }
        close_window "$2"
        exit 0
        ;;
    --new)
        [[ -n "${2:-}" ]] || { usage >&2; exit 2; }
        restore_all
        create_window "$2" new ''
        attach_window "$2"
        ;;
    --*) usage >&2; exit 2 ;;
esac

restore_all

window="${1:-}"
resume_ref="${2:-}"
if [[ -z "$window" ]]; then
    first_active=
    for marker in "$windows_dir"/*/active; do
        [[ -f "$marker" ]] || continue
        first_active="${marker%/active}"
        first_active="${first_active##*/}"
        break
    done
    window="${first_active:-main}"
fi
create_window "$window" resume "$resume_ref"
attach_window "$window"
