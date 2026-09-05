#!/bin/bash
set -euo pipefail

config_path() {
    printf '%s\n' "${TACC_VISTA_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/tacc-vista/config}"
}

load_config() {
    local file="${1:-$(config_path)}"
    local line key value

    if [[ ! -r "$file" ]]; then
        printf 'Configuration is not readable: %s\n' "$file" >&2
        return 1
    fi

    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        [[ -z "$line" || "$line" == \#* ]] && continue
        if [[ "$line" != *=* ]]; then
            printf 'Invalid configuration line (expected KEY=VALUE).\n' >&2
            return 1
        fi
        key="${line%%=*}"
        value="${line#*=}"
        key="${key//[[:space:]]/}"
        case "$key" in
            LOGIN_ALIAS|COMPUTE_ALIAS|VISTA_LOGIN_HOST|TACC_USERNAME|SSH_KEY_PATH|\
            SCHEDULER_ACCOUNT|REMOTE_PROJECT_DIR|ALLOCATION_JOB_NAME|TMUX_SESSION_NAME|\
            PARTITIONS|PARTITION_LIMITS|PARTITION_NODE_LIMITS|DEFAULT_PARTITION|DEFAULT_HOURS|PREFERRED_IDE|\
            LOGIN_NODE_PATTERN)
                printf -v "$key" '%s' "$value"
                ;;
            *)
                printf 'Unknown configuration key: %s\n' "$key" >&2
                return 1
                ;;
        esac
    done <"$file"

    TACC_VISTA_CONFIG_FILE="$file"
}

require_config_vars() {
    local name value
    for name in "$@"; do
        value="${!name-}"
        if [[ -z "$value" ]]; then
            printf 'Missing configuration value: %s\n' "$name" >&2
            return 1
        fi
        if [[ "$value" == *'<'*'>'* ]]; then
            printf 'Unresolved placeholder in configuration: %s\n' "$name" >&2
            return 1
        fi
    done
}

csv_contains() {
    local csv="$1" needle="$2" item
    local old_ifs="$IFS"
    IFS=','
    for item in $csv; do
        if [[ "$item" == "$needle" ]]; then
            IFS="$old_ifs"
            return 0
        fi
    done
    IFS="$old_ifs"
    return 1
}

partition_limit_hours() {
    local limits="$1" needle="$2" pair name hours
    local old_ifs="$IFS"
    IFS=','
    for pair in $limits; do
        name="${pair%%:*}"
        hours="${pair#*:}"
        if [[ "$name" == "$needle" && "$hours" =~ ^[0-9]+$ ]]; then
            IFS="$old_ifs"
            printf '%s\n' "$hours"
            return 0
        fi
    done
    IFS="$old_ifs"
    return 1
}

partition_limit_nodes() {
    local limits="$1" needle="$2" pair name nodes
    local old_ifs="$IFS"
    IFS=','
    for pair in $limits; do
        name="${pair%%:*}"
        nodes="${pair#*:}"
        if [[ "$name" == "$needle" && "$nodes" =~ ^[0-9]+$ ]]; then
            IFS="$old_ifs"
            printf '%s\n' "$nodes"
            return 0
        fi
    done
    IFS="$old_ifs"
    return 1
}

normalize_walltime() {
    local input="$1" hours minutes seconds
    if [[ "$input" =~ ^[0-9]+$ ]]; then
        hours=$((10#$input))
        if (( hours < 1 )); then
            printf '%s\n' 'Hours must be at least 1.' >&2
            return 2
        fi
        printf '%02d:00:00\n' "$hours"
        return 0
    fi
    if [[ ! "$input" =~ ^[0-9]+:[0-9]{2}:[0-9]{2}$ ]]; then
        printf 'Invalid wall time: %s (expected hours or HH:MM:SS)\n' "$input" >&2
        return 2
    fi
    IFS=: read -r hours minutes seconds <<<"$input"
    if (( 10#$minutes >= 60 || 10#$seconds >= 60 )); then
        printf 'Invalid wall time: %s\n' "$input" >&2
        return 2
    fi
    printf '%02d:%02d:%02d\n' "$((10#$hours))" "$((10#$minutes))" "$((10#$seconds))"
}

walltime_seconds() {
    local walltime="$1" hours minutes seconds
    IFS=: read -r hours minutes seconds <<<"$walltime"
    printf '%s\n' "$((10#$hours * 3600 + 10#$minutes * 60 + 10#$seconds))"
}

slurm_time_limit_seconds() {
    local value="$1" days=0 hours minutes seconds
    if [[ "$value" == *-* ]]; then
        days="${value%%-*}"
        value="${value#*-}"
    fi
    if [[ ! "$days" =~ ^[0-9]+$ || ! "$value" =~ ^[0-9]+:[0-9]{2}:[0-9]{2}$ ]]; then
        return 1
    fi
    IFS=: read -r hours minutes seconds <<<"$value"
    printf '%s\n' "$((10#$days * 86400 + 10#$hours * 3600 + 10#$minutes * 60 + 10#$seconds))"
}
