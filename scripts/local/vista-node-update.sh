#!/bin/bash
set -euo pipefail

lib_dir="$HOME/.local/lib/tacc-vista"
# shellcheck source=/dev/null
source "$lib_dir/common.sh"
load_config
require_config_vars LOGIN_ALIAS COMPUTE_ALIAS

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
mkdir -p "$state_dir"
chmod 700 "$state_dir"
temp_file="$(mktemp "$state_dir/.current-node.conf.XXXXXX")"
trap 'rm -f "$temp_file"' EXIT
printf 'Host %s\n    HostName %s\n' "$COMPUTE_ALIAS" "$node" >"$temp_file"
chmod 600 "$temp_file"
mv -f "$temp_file" "$current_config"
trap - EXIT
printf '%s\n' 'The compute SSH alias now points to the running allocation.'
