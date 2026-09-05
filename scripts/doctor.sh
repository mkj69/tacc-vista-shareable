#!/bin/bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$script_dir/common.sh"

check_remote=0
case "${1:-}" in
    --remote) check_remote=1 ;;
    --help|-h) printf 'Usage: %s [--remote]\n' "$0"; exit 0 ;;
    '') ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
esac

failures=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; failures=$((failures + 1)); }

if load_config && require_config_vars \
    LOGIN_ALIAS COMPUTE_ALIAS VISTA_LOGIN_HOST TACC_USERNAME SSH_KEY_PATH \
    SCHEDULER_ACCOUNT REMOTE_PROJECT_DIR ALLOCATION_JOB_NAME TMUX_SESSION_NAME \
    PARTITIONS PARTITION_LIMITS DEFAULT_PARTITION DEFAULT_HOURS PREFERRED_IDE LOGIN_NODE_PATTERN; then
    pass 'external configuration is complete'
else
    fail 'external configuration is incomplete'
fi

for command_name in bash ssh scp awk sed grep mktemp; do
    if command -v "$command_name" >/dev/null 2>&1; then
        pass "local command is available: $command_name"
    else
        fail "local command is missing: $command_name"
    fi
done

if [[ -n "${SSH_KEY_PATH-}" ]]; then
    expanded_key_path="$SSH_KEY_PATH"
    [[ "$expanded_key_path" != '~/'* ]] || expanded_key_path="$HOME/${expanded_key_path#\~/}"
    if [[ -f "$expanded_key_path" ]]; then
        pass 'configured SSH identity file exists'
    else
        fail 'configured SSH identity file does not exist'
    fi
fi

if [[ -n "${TACC_VISTA_CONFIG_FILE-}" ]]; then
    config_mode="$(stat -f '%Lp' "$TACC_VISTA_CONFIG_FILE" 2>/dev/null || stat -c '%a' "$TACC_VISTA_CONFIG_FILE" 2>/dev/null || true)"
    if [[ "$config_mode" =~ ^[0-7]+00$ ]]; then
        pass 'external configuration permissions are user-restricted'
    else
        fail 'external configuration must not be readable by group or other users'
    fi
fi

for path in \
    "$HOME/.local/bin/vista-allocate" \
    "$HOME/.local/bin/vista-node-update.sh" \
    "$HOME/.local/lib/tacc-vista/common.sh" \
    "$HOME/.ssh/tacc-vista/config"; do
    if [[ -e "$path" ]]; then pass 'installed local component is present'; else fail 'an installed local component is missing'; fi
done

if [[ -n "${LOGIN_ALIAS-}" ]] && ssh -F "$HOME/.ssh/config" -G "$LOGIN_ALIAS" >/dev/null 2>&1; then
    pass 'login SSH alias parses successfully'
else
    fail 'login SSH alias does not parse'
fi
if [[ -n "${COMPUTE_ALIAS-}" ]] && ssh -F "$HOME/.ssh/config" -G "$COMPUTE_ALIAS" >/dev/null 2>&1; then
    pass 'compute SSH alias parses successfully'
else
    fail 'compute SSH alias does not parse'
fi

if (( check_remote )); then
    if [[ -z "${LOGIN_ALIAS-}" ]]; then
        fail 'remote checks require a valid login alias'
    elif ssh -T "$LOGIN_ALIAS" '
        set -eu
        lib="$HOME/.local/lib/tacc-vista"
        test -r "$HOME/.config/tacc-vista/config"
        . "$lib/common.sh"
        load_config "$HOME/.config/tacc-vista/config"
        require_config_vars REMOTE_PROJECT_DIR
        test -d "$REMOTE_PROJECT_DIR"
        for file in common.sh submit-node.sh resolve-node.sh codex-start.sh run-codex.sh start.sh; do
            test -x "$lib/$file"
            bash -n "$lib/$file"
        done
        for command_name in bash sbatch squeue scontrol python3 pgrep readlink codex; do
            command -v "$command_name" >/dev/null 2>&1
        done
        command -v tmux >/dev/null 2>&1 || command -v screen >/dev/null 2>&1
    '; then
        pass 'remote helpers are installed'
        pass 'remote helper syntax and dependencies are valid'
    else
        fail 'remote helper check failed'
    fi
fi

if (( failures )); then
    printf 'Doctor found %s problem(s).\n' "$failures" >&2
    exit 1
fi
printf '%s\n' 'Doctor completed successfully.'
