#!/bin/bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/tacc-vista-smoke.XXXXXX")"
trap 'rm -rf "$test_root"' EXIT
test_home="$test_root/home"
config_file="$test_root/config"
mkdir -p "$test_home"
mkdir -p "$test_home/.ssh"
: >"$test_home/.ssh/test-key"
chmod 600 "$test_home/.ssh/test-key"

cat >"$config_file" <<'CONFIG'
LOGIN_ALIAS=login-test
COMPUTE_ALIAS=compute-test
VISTA_LOGIN_HOST=login.invalid
TACC_USERNAME=test-user
SSH_KEY_PATH=~/.ssh/test-key
SCHEDULER_ACCOUNT=test-account
REMOTE_PROJECT_DIR=/shared/test-project
ALLOCATION_JOB_NAME=allocation-test
TMUX_SESSION_NAME=codex-test
PARTITIONS=dev,test
PARTITION_LIMITS=dev:2,test:48
PARTITION_NODE_LIMITS=dev:8,test:32
DEFAULT_PARTITION=dev
DEFAULT_HOURS=2
PREFERRED_IDE=none
LOGIN_NODE_PATTERN=login*
CONFIG
chmod 600 "$config_file"

HOME="$test_home" TACC_VISTA_CONFIG="$config_file" "$repo_dir/scripts/install.sh" --local-only
HOME="$test_home" TACC_VISTA_CONFIG="$config_file" "$repo_dir/scripts/install.sh" --local-only >/dev/null
bash -n \
    "$test_home/.local/bin/vista-allocate" \
    "$test_home/.local/bin/vista-node-update.sh" \
    "$test_home/.local/lib/tacc-vista/common.sh" \
    "$repo_dir/scripts/install.sh" \
    "$repo_dir/scripts/doctor.sh" \
    "$repo_dir/scripts/remote/submit-node.sh" \
    "$repo_dir/scripts/remote/resolve-node.sh" \
    "$repo_dir/scripts/remote/codex-start.sh" \
    "$repo_dir/scripts/remote/run-codex.sh" \
    "$repo_dir/scripts/remote/start.sh"
HOME="$test_home" TACC_VISTA_CONFIG="$config_file" "$repo_dir/scripts/doctor.sh"
if HOME="$test_home" TACC_VISTA_CONFIG="$config_file" "$test_home/.local/bin/vista-allocate" invalid 1 none >/dev/null 2>&1; then
    printf '%s\n' 'Expected invalid partition validation to fail.' >&2
    exit 1
fi
if HOME="$test_home" TACC_VISTA_CONFIG="$config_file" "$test_home/.local/bin/vista-allocate" dev 1 9 none >/dev/null 2>&1; then
    printf '%s\n' 'Expected excessive node-count validation to fail.' >&2
    exit 1
fi
grep -Fq 'Host login-test' "$test_home/.ssh/tacc-vista/config"
grep -Fq 'Host compute-test' "$test_home/.ssh/tacc-vista/config"
grep -Fq 'vista-node-not-ready.invalid' "$test_home/.ssh/tacc-vista/current-node.conf"
head -n 1 "$test_home/.ssh/config" | grep -Fq 'Include '
test "$(grep -Fc 'Include ' "$test_home/.ssh/config")" -eq 1
grep -Fq '"$@"' "$repo_dir/scripts/remote/start.sh"
grep -Fq 'codex resume "$resume_ref"' "$repo_dir/scripts/remote/run-codex.sh"
grep -Fq 'rm -f "$window_dir/active"' "$repo_dir/scripts/remote/run-codex.sh"
grep -Fq 'exec "$editor_cli" --classic --new-window --folder-uri "$remote_uri"' "$repo_dir/scripts/local/vista-allocate"
grep -Fq 'allocation_alias="${COMPUTE_ALIAS}-${job_id}"' "$repo_dir/scripts/local/vista-allocate"
grep -Fq 'allocation_alias="${COMPUTE_ALIAS}-${job_id}"' "$repo_dir/scripts/local/vista-node-update.sh"
grep -Fq "submit-node.sh '\$partition' '\$walltime' '\$nodes'" "$repo_dir/scripts/local/vista-allocate"
grep -Fq -- '--nodes="$nodes"' "$repo_dir/scripts/remote/submit-node.sh"
grep -Fq -- '--ntasks="$nodes"' "$repo_dir/scripts/remote/submit-node.sh"
printf '%s\n' 'Smoke test passed.'
