#!/bin/bash
set -euo pipefail

lib_dir="$HOME/.local/lib/tacc-vista"
# shellcheck source=/dev/null
source "$lib_dir/common.sh"
load_config "$HOME/.config/tacc-vista/config"
require_config_vars ALLOCATION_JOB_NAME

requested_job_id="${1:-}"
output_mode="${2:-first}"
if [[ -n "$requested_job_id" && ! "$requested_job_id" =~ ^[0-9]+$ ]]; then
    printf '%s\n' 'Invalid Slurm job ID.' >&2
    exit 2
fi
case "$output_mode" in
    first|all) ;;
    *) printf '%s\n' 'Output mode must be first or all.' >&2; exit 2 ;;
esac

if [[ -n "$requested_job_id" ]]; then
    job_line="$(squeue -h -j "$requested_job_id" -o '%i|%j|%T|%N' 2>/dev/null | head -n 1 || true)"
    if [[ -z "$job_line" ]]; then
        printf '%s\n' 'The requested allocation is no longer pending or running.' >&2
        exit 76
    fi
else
    job_line="$(squeue -h -u "$USER" -n "$ALLOCATION_JOB_NAME" -t R --sort=i -o '%i|%j|%T|%N' | head -n 1 || true)"
fi
[[ -z "$job_line" ]] && exit 75

IFS='|' read -r job_id job_name job_state node_list <<<"$job_line"
case "$job_state" in
    RUNNING) ;;
    PENDING|CONFIGURING) exit 75 ;;
    *) printf '%s\n' 'The requested allocation entered a terminal or unusable state.' >&2; exit 76 ;;
esac

nodes="$(scontrol show hostnames "$node_list")"
[[ -z "$nodes" ]] && exit 75
first_node="$(printf '%s\n' "$nodes" | head -n 1)"
printf '%s\n' 'Resolved the running compute node.' >&2
if [[ "$output_mode" == all ]]; then
    printf '%s\n' "$nodes"
else
    printf '%s\n' "$first_node"
fi
