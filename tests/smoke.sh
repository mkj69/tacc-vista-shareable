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
    "$test_home/.local/bin/vista-open-all" \
    "$test_home/.local/bin/vista-node-update.sh" \
    "$test_home/.local/bin/vista-dashboard-open" \
    "$test_home/.local/lib/tacc-vista/common.sh" \
    "$repo_dir/scripts/install.sh" \
    "$repo_dir/scripts/doctor.sh" \
    "$repo_dir/scripts/remote/submit-node.sh" \
    "$repo_dir/scripts/remote/resolve-node.sh" \
    "$repo_dir/scripts/remote/codex-start.sh" \
    "$repo_dir/scripts/remote/run-codex.sh" \
    "$repo_dir/scripts/remote/start.sh" \
    "$repo_dir/scripts/remote/dashboard-start.sh"
python3 -c 'import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$repo_dir/scripts/dashboard/vista_job_dashboard.py"
PYTHONPYCACHEPREFIX="$test_root/pycache" python3 "$repo_dir/tests/test_dashboard_cancel.py"
rendered_dashboard="$test_root/rendered-dashboard.py"
sed -e 's/__LOGIN_ALIAS__/login-test/g' -e 's/__COMPUTE_ALIAS__/compute-test/g' \
    "$repo_dir/scripts/dashboard/vista_job_dashboard.py" >"$rendered_dashboard"
if grep -Eq '__LOGIN_ALIAS__|__COMPUTE_ALIAS__' "$rendered_dashboard"; then
    printf '%s\n' 'Expected dashboard aliases to be rendered during installation.' >&2
    exit 1
fi
HOME="$test_home" TACC_VISTA_CONFIG="$config_file" "$repo_dir/scripts/doctor.sh"
grep -Fq 'Host login-test' "$test_home/.ssh/tacc-vista/config"
grep -Fq 'Host compute-test' "$test_home/.ssh/tacc-vista/config"
grep -Fq 'vista-node-not-ready.invalid' "$test_home/.ssh/tacc-vista/current-node.conf"
head -n 1 "$test_home/.ssh/config" | grep -Fq 'Include '
test "$(grep -Fc 'Include ' "$test_home/.ssh/config")" -eq 1
fake_bin="$test_root/bin"
mkdir -p "$fake_bin"
cat >"$fake_bin/ssh" <<'FAKESSH'
#!/bin/bash
printf '%s\n' compute-a.invalid compute-b.invalid
FAKESSH
cat >"$fake_bin/code" <<'FAKECODE'
#!/bin/bash
printf '%s\n' "$*" >>"$TEST_CODE_LOG"
FAKECODE
chmod +x "$fake_bin/ssh" "$fake_bin/code"
PATH="$fake_bin:$PATH" HOME="$test_home" TACC_VISTA_CONFIG="$config_file" \
    "$test_home/.local/bin/vista-node-update.sh" 12345 >/dev/null
grep -Fq 'Host compute-test-12345' "$test_home/.ssh/tacc-vista/allocations/12345.conf"
grep -Fq 'Host compute-test-12345-n1' "$test_home/.ssh/tacc-vista/allocations/12345.conf"
grep -Fq 'Host compute-test-12345-n2' "$test_home/.ssh/tacc-vista/allocations/12345.conf"
grep -Fxq 'compute-test-12345-n1' "$test_home/.ssh/tacc-vista/allocations/12345.alias"
grep -Fxq 'compute-test-12345-n2' "$test_home/.ssh/tacc-vista/allocations/12345.alias"
code_log="$test_root/code.log"
PATH="$fake_bin:$PATH" HOME="$test_home" TACC_VISTA_CONFIG="$config_file" TEST_CODE_LOG="$code_log" \
    "$test_home/.local/bin/vista-open-all" 12345 code >/dev/null
test "$(wc -l <"$code_log" | tr -d ' ')" -eq 2
grep -Fq 'ssh-remote+compute-test-12345-n1/shared/test-project' "$code_log"
grep -Fq 'ssh-remote+compute-test-12345-n2/shared/test-project' "$code_log"
if grep -Eq 'submit-node|sbatch' "$repo_dir/scripts/local/vista-open-all"; then
    printf '%s\n' 'Open-only helper must not contain a submission path.' >&2
    exit 1
fi
if HOME="$test_home" TACC_VISTA_CONFIG="$config_file" "$test_home/.local/bin/vista-allocate" invalid 1 none >/dev/null 2>&1; then
    printf '%s\n' 'Expected invalid partition validation to fail.' >&2
    exit 1
fi
if HOME="$test_home" TACC_VISTA_CONFIG="$config_file" "$test_home/.local/bin/vista-allocate" dev 1 9 none >/dev/null 2>&1; then
    printf '%s\n' 'Expected excessive node-count validation to fail.' >&2
    exit 1
fi
grep -Fq '"$@"' "$repo_dir/scripts/remote/start.sh"
grep -Fq 'codex resume "$resume_ref"' "$repo_dir/scripts/remote/run-codex.sh"
grep -Fq 'rm -f "$window_dir/active"' "$repo_dir/scripts/remote/run-codex.sh"
grep -Fq 'exec "$editor_cli" --classic --new-window --folder-uri "$remote_uri"' "$repo_dir/scripts/local/vista-allocate"
grep -Fq 'allocation_alias="${COMPUTE_ALIAS}-${job_id}"' "$repo_dir/scripts/local/vista-allocate"
grep -Fq 'allocation_alias="${COMPUTE_ALIAS}-${job_id}"' "$repo_dir/scripts/local/vista-node-update.sh"
grep -Fq 'cursor-all' "$repo_dir/scripts/local/vista-allocate"
grep -Fq 'code-all' "$repo_dir/scripts/local/vista-allocate"
grep -Fq 'no Slurm job will be submitted' "$repo_dir/scripts/local/vista-open-all"
grep -Fq "remote_command+=\" '\$job_id' all\"" "$repo_dir/scripts/local/vista-node-update.sh"
grep -Fq 'vista-dashboard-open' "$repo_dir/scripts/local/vista-allocate"
grep -Fq 'ssh -O forward' "$repo_dir/scripts/local/vista-dashboard-open"
grep -Fq '__LOGIN_ALIAS__' "$repo_dir/scripts/dashboard/vista_job_dashboard.py"
grep -Fq '__COMPUTE_ALIAS__' "$repo_dir/scripts/dashboard/vista_job_dashboard.py"
grep -Fq 'renderCpuCharts' "$repo_dir/scripts/dashboard/vista_job_dashboard.py"
grep -Fq 'renderGpuCharts' "$repo_dir/scripts/dashboard/vista_job_dashboard.py"
grep -Fq 'openDetailKeys' "$repo_dir/scripts/dashboard/vista_job_dashboard.py"
grep -Fq 'request_job_cancel' "$repo_dir/scripts/dashboard/vista_job_dashboard.py"
grep -Fq 'X-Vista-CSRF' "$repo_dir/scripts/dashboard/vista_job_dashboard.py"
grep -Fq 'cancel-job' "$repo_dir/scripts/dashboard/vista_job_dashboard.py"
grep -Fq "submit-node.sh '\$partition' '\$walltime' '\$nodes'" "$repo_dir/scripts/local/vista-allocate"
grep -Fq -- '--nodes="$nodes"' "$repo_dir/scripts/remote/submit-node.sh"
grep -Fq -- '--ntasks="$nodes"' "$repo_dir/scripts/remote/submit-node.sh"
printf '%s\n' 'Smoke test passed.'
