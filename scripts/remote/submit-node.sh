#!/bin/bash
set -euo pipefail

lib_dir="$HOME/.local/lib/tacc-vista"
# shellcheck source=/dev/null
source "$lib_dir/common.sh"
load_config "$HOME/.config/tacc-vista/config"
require_config_vars SCHEDULER_ACCOUNT ALLOCATION_JOB_NAME PARTITIONS PARTITION_LIMITS PARTITION_NODE_LIMITS DEFAULT_PARTITION DEFAULT_HOURS

partition="${1:-$DEFAULT_PARTITION}"
time_input="${2:-$DEFAULT_HOURS}"
nodes="${3:-1}"
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
if [[ ! "$nodes" =~ ^[0-9]+$ ]] || (( 10#$nodes < 1 )); then
    printf '%s\n' 'Requested node count must be a positive integer.' >&2
    exit 2
fi
nodes=$((10#$nodes))
limit_nodes="$(partition_limit_nodes "$PARTITION_NODE_LIMITS" "$partition")" || {
    printf '%s\n' 'The requested partition has no configured node limit.' >&2
    exit 2
}
if (( nodes > 10#$limit_nodes )); then
    printf '%s\n' 'Requested node count exceeds the configured partition limit.' >&2
    exit 2
fi

requested_seconds="$(walltime_seconds "$walltime")"
existing_lines="$(squeue -h -u "$USER" -n "$ALLOCATION_JOB_NAME" -t R,PD,CF --sort=i -o '%i|%T|%N|%P|%D|%l' || true)"
while IFS='|' read -r job_id job_state node_list existing_partition existing_nodes existing_time; do
    [[ -n "$job_id" ]] || continue
    existing_seconds="$(slurm_time_limit_seconds "$existing_time" || true)"
    if [[ "$existing_partition" == "$partition" && "$existing_nodes" == "$nodes" && "$existing_seconds" == "$requested_seconds" ]]; then
        printf 'Reusing the matching configured allocation (%s, %s node(s)).\n' "$job_state" "$existing_nodes" >&2
        printf '%s\n' "$job_id"
        exit 0
    fi
done <<<"$existing_lines"

if [[ -n "$existing_lines" ]]; then
    printf '%s\n' 'Existing allocation(s) do not match the requested partition, wall time, and node count; submitting the explicitly requested allocation.' >&2
fi

state_dir="$HOME/.cache/tacc-vista"
mkdir -p "$state_dir"
if ! submit_output="$(sbatch \
    --parsable \
    --account="$SCHEDULER_ACCOUNT" \
    --partition="$partition" \
    --nodes="$nodes" \
    --ntasks="$nodes" \
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
printf 'Submitted the configured %s-node allocation.\n' "$nodes" >&2
printf '%s\n' "$job_id"
