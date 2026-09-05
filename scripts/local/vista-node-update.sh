#!/bin/bash
set -euo pipefail

lib_dir="$HOME/.local/lib/tacc-vista"
# shellcheck source=/dev/null
source "$lib_dir/common.sh"
load_config
require_config_vars LOGIN_ALIAS COMPUTE_ALIAS TACC_USERNAME SSH_KEY_PATH

job_id="${1:-}"
if [[ -n "$job_id" && ! "$job_id" =~ ^[0-9]+$ ]]; then
    printf '%s\n' 'Invalid Slurm job ID.' >&2
    exit 2
fi

remote_command='$HOME/.local/lib/tacc-vista/resolve-node.sh'
if [[ -n "$job_id" ]]; then
    remote_command+=" '$job_id'"
fi

reported_wait=0
while :; do
    if node="$(ssh -T "$LOGIN_ALIAS" "$remote_command")"; then
        break
    else
        resolver_status=$?
    fi
    if (( resolver_status != 75 )); then
        exit "$resolver_status"
    fi
    if (( ! reported_wait )); then
        printf '%s\n' 'The allocation is not running yet; waiting for Slurm...' >&2
        reported_wait=1
    fi
    sleep 5
done

if [[ ! "$node" =~ ^[A-Za-z0-9.-]+$ ]]; then
    printf '%s\n' 'The scheduler returned an invalid node name.' >&2
    exit 1
fi

state_dir="$HOME/.ssh/tacc-vista"
current_config="$state_dir/current-node.conf"
allocation_dir="$state_dir/allocations"
mkdir -p "$state_dir" "$allocation_dir"
chmod 700 "$state_dir" "$allocation_dir"

allocation_alias="${COMPUTE_ALIAS}-${job_id}"
allocation_config="$allocation_dir/$job_id.conf"
alias_file="$allocation_dir/$job_id.alias"
allocation_tmp="$(mktemp "$allocation_dir/.$job_id.conf.XXXXXX")"
alias_tmp="$(mktemp "$allocation_dir/.$job_id.alias.XXXXXX")"
current_tmp="$(mktemp "$state_dir/.current-node.conf.XXXXXX")"
cleanup() {
    [[ -z "$allocation_tmp" ]] || rm -f "$allocation_tmp"
    [[ -z "$alias_tmp" ]] || rm -f "$alias_tmp"
    [[ -z "$current_tmp" ]] || rm -f "$current_tmp"
}
trap cleanup EXIT

{
    printf 'Host %s\n' "$allocation_alias"
    printf '    HostName %s\n' "$node"
    printf '    User %s\n' "$TACC_USERNAME"
    printf '    IdentityFile "%s"\n' "$SSH_KEY_PATH"
    printf '%s\n' '    IdentitiesOnly yes'
    printf '    ProxyJump %s\n' "$LOGIN_ALIAS"
    printf '%s\n' \
        '    StrictHostKeyChecking accept-new' \
        '    CheckHostIP no' \
        '    ServerAliveInterval 30' \
        '    ServerAliveCountMax 3'
    printf '\n'
} >"$allocation_tmp"
printf '%s\n' "$allocation_alias" >"$alias_tmp"
chmod 600 "$allocation_tmp" "$alias_tmp"
mv -f "$allocation_tmp" "$allocation_config"
mv -f "$alias_tmp" "$alias_file"
allocation_tmp=''
alias_tmp=''

printf 'Host %s\n    HostName %s\n\n' "$COMPUTE_ALIAS" "$node" >"$current_tmp"
for fragment in "$allocation_dir"/*.conf; do
    [[ -f "$fragment" ]] || continue
    cat "$fragment" >>"$current_tmp"
done
chmod 600 "$current_tmp"
mv -f "$current_tmp" "$current_config"
current_tmp=''
trap - EXIT
printf '%s\n' 'The compute SSH alias now points to the running allocation.'
printf 'Allocation-specific SSH alias: %s\n' "$allocation_alias"
