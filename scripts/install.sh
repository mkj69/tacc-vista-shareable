#!/bin/bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$script_dir/common.sh"

remote_install=1
case "${1:-}" in
    --local-only)
        remote_install=0
        shift
        ;;
    --help|-h)
        printf 'Usage: %s [--local-only]\n' "$0"
        exit 0
        ;;
    '') ;;
    *)
        printf 'Unknown option: %s\n' "$1" >&2
        exit 2
        ;;
esac

load_config
require_config_vars \
    LOGIN_ALIAS COMPUTE_ALIAS VISTA_LOGIN_HOST TACC_USERNAME SSH_KEY_PATH \
    SCHEDULER_ACCOUNT REMOTE_PROJECT_DIR ALLOCATION_JOB_NAME TMUX_SESSION_NAME \
    PARTITIONS PARTITION_LIMITS PARTITION_NODE_LIMITS DEFAULT_PARTITION DEFAULT_HOURS PREFERRED_IDE \
    LOGIN_NODE_PATTERN

for identifier in "$LOGIN_ALIAS" "$COMPUTE_ALIAS" "$VISTA_LOGIN_HOST" "$TACC_USERNAME" "$ALLOCATION_JOB_NAME"; do
    if [[ ! "$identifier" =~ ^[A-Za-z0-9._-]+$ ]]; then
        printf 'Unsafe identifier in configuration.\n' >&2
        exit 2
    fi
done
if ! csv_contains "$PARTITIONS" "$DEFAULT_PARTITION"; then
    printf '%s\n' 'DEFAULT_PARTITION is not listed in PARTITIONS.' >&2
    exit 2
fi
default_walltime="$(normalize_walltime "$DEFAULT_HOURS")"
limit_hours="$(partition_limit_hours "$PARTITION_LIMITS" "$DEFAULT_PARTITION")" || {
    printf '%s\n' 'DEFAULT_PARTITION has no valid PARTITION_LIMITS entry.' >&2
    exit 2
}
if (( $(walltime_seconds "$default_walltime") > 10#$limit_hours * 3600 )); then
    printf '%s\n' 'DEFAULT_HOURS exceeds the configured partition limit.' >&2
    exit 2
fi
default_node_limit="$(partition_limit_nodes "$PARTITION_NODE_LIMITS" "$DEFAULT_PARTITION")" || {
    printf '%s\n' 'DEFAULT_PARTITION has no valid PARTITION_NODE_LIMITS entry.' >&2
    exit 2
}
if (( 10#$default_node_limit < 1 )); then
    printf '%s\n' 'The configured default partition must allow at least one node.' >&2
    exit 2
fi
case "$PREFERRED_IDE" in cursor|code|none) ;; *) printf '%s\n' 'PREFERRED_IDE must be cursor, code, or none.' >&2; exit 2 ;; esac

ssh_dir="$HOME/.ssh"
state_dir="$ssh_dir/tacc-vista"
ssh_config="$ssh_dir/config"
ssh_fragment="$state_dir/config"
node_include="$state_dir/current-node.conf"
local_bin="$HOME/.local/bin"
local_lib="$HOME/.local/lib/tacc-vista"
profile_file="$HOME/.zprofile"

mkdir -p "$ssh_dir" "$state_dir" "$local_bin" "$local_lib"
chmod 700 "$ssh_dir" "$state_dir" "$local_bin" "$local_lib"

install -m 700 "$script_dir/common.sh" "$local_lib/common.sh"
install -m 700 "$script_dir/local/vista-allocate" "$local_bin/vista-allocate"
install -m 700 "$script_dir/local/vista-node-update.sh" "$local_bin/vista-node-update.sh"
install -m 700 "$script_dir/local/vista-dashboard-open" "$local_bin/vista-dashboard-open"

fragment_tmp="$(mktemp "$state_dir/.config.XXXXXX")"
node_tmp=''
remote_config_tmp=''
dashboard_render_tmp=''
ssh_config_tmp=''
cleanup() {
    [[ -z "$fragment_tmp" ]] || rm -f "$fragment_tmp"
    [[ -z "$node_tmp" ]] || rm -f "$node_tmp"
    [[ -z "$remote_config_tmp" ]] || rm -f "$remote_config_tmp"
    [[ -z "$dashboard_render_tmp" ]] || rm -f "$dashboard_render_tmp"
    [[ -z "$ssh_config_tmp" ]] || rm -f "$ssh_config_tmp"
}
trap cleanup EXIT
{
    printf 'Include "%s"\n\n' "$node_include"
    printf 'Host %s\n' "$LOGIN_ALIAS"
    printf '    HostName %s\n' "$VISTA_LOGIN_HOST"
    printf '    User %s\n' "$TACC_USERNAME"
    printf '    IdentityFile "%s"\n' "$SSH_KEY_PATH"
    printf '%s\n' '    IdentitiesOnly yes' '    AddKeysToAgent yes'
    if [[ "$(uname -s)" == Darwin ]]; then
        printf '%s\n' '    UseKeychain yes'
    fi
    printf '%s\n' '    ControlMaster auto'
    printf '    ControlPath "%s"\n' "$state_dir/cm-%C"
    printf '%s\n\n' '    ControlPersist yes' '    ServerAliveInterval 60' '    ServerAliveCountMax 3'
    printf 'Host %s\n' "$COMPUTE_ALIAS"
    printf '    User %s\n' "$TACC_USERNAME"
    printf '    IdentityFile "%s"\n' "$SSH_KEY_PATH"
    printf '%s\n' '    IdentitiesOnly yes'
    printf '    ProxyJump %s\n' "$LOGIN_ALIAS"
    printf '%s\n' '    StrictHostKeyChecking accept-new' '    CheckHostIP no' '    ServerAliveInterval 30' '    ServerAliveCountMax 3'
} >"$fragment_tmp"
chmod 600 "$fragment_tmp"
mv -f "$fragment_tmp" "$ssh_fragment"

if [[ ! -e "$node_include" ]]; then
    node_tmp="$(mktemp "$state_dir/.current-node.conf.XXXXXX")"
    printf 'Host %s\n    HostName vista-node-not-ready.invalid\n' "$COMPUTE_ALIAS" >"$node_tmp"
    chmod 600 "$node_tmp"
    mv -f "$node_tmp" "$node_include"
    node_tmp=''
fi

if [[ ! -e "$ssh_config" ]]; then
    : >"$ssh_config"
    chmod 600 "$ssh_config"
fi
include_line="Include \"$ssh_fragment\""
if ! grep -Fqx "$include_line" "$ssh_config"; then
    if [[ -s "$ssh_config" ]]; then
        cp -p "$ssh_config" "$ssh_config.tacc-vista.bak.$(date +%Y%m%d%H%M%S)"
    fi
    ssh_config_tmp="$(mktemp "$ssh_dir/.config.XXXXXX")"
    {
        printf '%s\n\n' "$include_line"
        cat "$ssh_config"
    } >"$ssh_config_tmp"
    chmod 600 "$ssh_config_tmp"
    mv -f "$ssh_config_tmp" "$ssh_config"
    ssh_config_tmp=''
fi

if [[ ! -e "$profile_file" ]]; then
    : >"$profile_file"
fi
if ! grep -Fq '$HOME/.local/bin' "$profile_file"; then
    printf '%s\n' 'export PATH="$HOME/.local/bin:$PATH"' >>"$profile_file"
fi

if (( remote_install )); then
    dashboard_render_tmp="$(mktemp "${TMPDIR:-/tmp}/tacc-vista-dashboard.XXXXXX")"
    sed \
        -e "s/__LOGIN_ALIAS__/$LOGIN_ALIAS/g" \
        -e "s/__COMPUTE_ALIAS__/$COMPUTE_ALIAS/g" \
        "$script_dir/dashboard/vista_job_dashboard.py" >"$dashboard_render_tmp"
    chmod 600 "$dashboard_render_tmp"

    remote_config_tmp="$(mktemp "${TMPDIR:-/tmp}/tacc-vista-remote-config.XXXXXX")"
    {
        printf 'SCHEDULER_ACCOUNT=%s\n' "$SCHEDULER_ACCOUNT"
        printf 'REMOTE_PROJECT_DIR=%s\n' "$REMOTE_PROJECT_DIR"
        printf 'ALLOCATION_JOB_NAME=%s\n' "$ALLOCATION_JOB_NAME"
        printf 'TMUX_SESSION_NAME=%s\n' "$TMUX_SESSION_NAME"
        printf 'PARTITIONS=%s\n' "$PARTITIONS"
        printf 'PARTITION_LIMITS=%s\n' "$PARTITION_LIMITS"
        printf 'PARTITION_NODE_LIMITS=%s\n' "$PARTITION_NODE_LIMITS"
        printf 'DEFAULT_PARTITION=%s\n' "$DEFAULT_PARTITION"
        printf 'DEFAULT_HOURS=%s\n' "$DEFAULT_HOURS"
        printf 'LOGIN_NODE_PATTERN=%s\n' "$LOGIN_NODE_PATTERN"
    } >"$remote_config_tmp"
    chmod 600 "$remote_config_tmp"

    ssh -T "$LOGIN_ALIAS" 'mkdir -p "$HOME/.local/lib/tacc-vista" "$HOME/.config/tacc-vista" && chmod 700 "$HOME/.local/lib/tacc-vista" "$HOME/.config/tacc-vista"'
    scp -q \
        "$script_dir/common.sh" \
        "$script_dir/remote/submit-node.sh" \
        "$script_dir/remote/resolve-node.sh" \
        "$script_dir/remote/codex-start.sh" \
        "$script_dir/remote/run-codex.sh" \
        "$script_dir/remote/start.sh" \
        "$script_dir/remote/dashboard-start.sh" \
        "$LOGIN_ALIAS:.local/lib/tacc-vista/"
    scp -q "$dashboard_render_tmp" "$LOGIN_ALIAS:.local/lib/tacc-vista/vista_job_dashboard.py.new"
    scp -q "$remote_config_tmp" "$LOGIN_ALIAS:.config/tacc-vista/config.new"
    ssh -T "$LOGIN_ALIAS" '
        set -eu
        chmod 700 "$HOME/.local/lib/tacc-vista/"*.sh "$HOME/.local/lib/tacc-vista/vista_job_dashboard.py.new"
        chmod 600 "$HOME/.config/tacc-vista/config.new"
        if [ -f "$HOME/.config/tacc-vista/config" ]; then
            cp -p "$HOME/.config/tacc-vista/config" "$HOME/.config/tacc-vista/config.bak.$(date +%Y%m%d%H%M%S)"
        fi
        mv -f "$HOME/.config/tacc-vista/config.new" "$HOME/.config/tacc-vista/config"
        mv -f "$HOME/.local/lib/tacc-vista/vista_job_dashboard.py.new" "$HOME/.local/lib/tacc-vista/vista_job_dashboard.py"
        if [ ! -e "$HOME/start.sh" ]; then
            ln -s "$HOME/.local/lib/tacc-vista/start.sh" "$HOME/start.sh"
        fi
    '
fi

trap - EXIT
cleanup
printf '%s\n' 'TACC Vista helpers installed successfully.'
printf '%s\n' 'Open a new shell, then run: vista-allocate [partition] [hours] [nodes] [cursor|code|none]'
printf '%s\n' 'Open the monitoring dashboard separately with: vista-dashboard-open'
if (( remote_install )); then
    printf '%s\n' 'On a compute node, run: ~/start.sh'
fi
