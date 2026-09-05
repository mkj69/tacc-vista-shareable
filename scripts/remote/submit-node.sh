#!/bin/bash
set -euo pipefail

lib_dir="$HOME/.local/lib/tacc-vista"
# shellcheck source=/dev/null
source "$lib_dir/common.sh"
load_config "$HOME/.config/tacc-vista/config"
require_config_vars SCHEDULER_ACCOUNT ALLOCATION_JOB_NAME PARTITIONS PARTITION_LIMITS DEFAULT_PARTITION DEFAULT_HOURS

partition="${1:-$DEFAULT_PARTITION}"
time_input="${2:-$DEFAULT_HOURS}"
if [[ ! "$partition" =~ ^[A-Za-z0-9._-]+$ ]] || ! csv_contains "$PARTITIONS" "$partition"; then
    printf '%s\n' 'Requested partition is not allowed by the remote configuration.' >&2
    exit 2
fi
walltime="$(normalize_walltime "$time_input")"
limit_hours="$(partition_limit_hours "$PARTITION_LIMITS" "$partition")" || {
    printf '%s\n' 'The requested partition has no configured time limit.' >&2
    exit 2
}
if (( $(walltime_seconds "$walltime") > 10#$limit_hours * 3600 )); then
    printf '%s\n' 'Requested wall time exceeds the configured partition limit.' >&2
    exit 2
fi

existing="$(squeue -h -u "$USER" -n "$ALLOCATION_JOB_NAME" -t R,PD,CF --sort=i -o '%i|%T|%N|%P' | head -n 1 || true)"
if [[ -n "$existing" ]]; then
    IFS='|' read -r job_id job_state node_list existing_partition <<<"$existing"
    if [[ "$existing_partition" != "$partition" ]]; then
        printf '%s\n' 'An active allocation exists in a different partition; refusing to reuse or duplicate it.' >&2
        exit 77
    fi
    printf 'Reusing the configured allocation (%s).\n' "$job_state" >&2
    printf '%s\n' "$job_id"
    exit 0
fi

state_dir="$HOME/.cache/tacc-vista"
mkdir -p "$state_dir"
if ! submit_output="$(sbatch \
    --parsable \
    --account="$SCHEDULER_ACCOUNT" \
    --partition="$partition" \
    --nodes=1 \
    --ntasks=1 \
    --time="$walltime" \
    --job-name="$ALLOCATION_JOB_NAME" \
    --output="$state_dir/allocation-%j.out" \
    --wrap='exec sleep infinity' 2>&1)"; then
    printf '%s\n' "$submit_output" >&2
    exit 1
fi

job_id="$(awk -F';' '/^[[:space:]]*[0-9]+(;|[[:space:]]*$)/ { gsub(/[[:space:]]/, "", $1); print $1; exit }' <<<"$submit_output")"
if [[ -z "$job_id" ]]; then
    printf '%s\n' "$submit_output" >&2
    printf '%s\n' 'Could not find a Slurm job ID in the submission output.' >&2
    exit 1
fi
printf '%s\n' 'Submitted the configured allocation.' >&2
printf '%s\n' "$job_id"
