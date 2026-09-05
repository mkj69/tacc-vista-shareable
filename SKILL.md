---
name: tacc-vista-shareable
description: Configure or operate a standalone, privacy-preserving TACC Vista SSH, Slurm allocation, IDE jump, CPU/GPU job dashboard, and remote Codex recovery workflow from placeholders. Use for portable setups that must not embed or disclose identifiers, paths, credentials, job data, or node data.
---

# TACC Vista Shareable

Build this standalone workflow on a user's machine:

```text
local login authentication -> remote Slurm allocation -> job dashboard -> dynamic compute SSH alias -> IDE
compute-node shell -> named tmux/screen windows -> exact Codex sessions
```

Start from the placeholders in [references/configuration-template.md](references/configuration-template.md). Copy [assets/tacc-vista.env.example](assets/tacc-vista.env.example) outside the skill before replacing placeholders. Use [assets/ssh-config.example](assets/ssh-config.example) only as a merge template; never overwrite an existing SSH config wholesale.

## Executable setup

For a first-time installation, help the user fill the external configuration, then run `scripts/install.sh`. The installer creates the local SSH fragment and helper commands, installs the remote allocation/recovery/dashboard helpers through the configured login alias, and leaves existing unrelated SSH settings intact. Run `scripts/doctor.sh --remote` afterward. Use `tests/smoke.sh` to validate the package itself without contacting Vista.

Do not run the installer until every placeholder has been replaced and the user has authorized changes to local and remote configuration. The installer never submits a Slurm allocation; that remains a separate explicit action.

## Privacy boundary

Never place real values for any of the following in this skill directory, a shareable archive, example output, source control, or a public response:

- names, usernames, email addresses, institution or allocation identifiers;
- home, project, work, or scratch paths;
- login endpoints, node names, job IDs, reservation names, or socket paths;
- token codes, passwords, private keys, key material, or session data.

Never read or copy private-key contents. Never automate a multifactor token. Let SSH prompt the user directly when fresh authentication is required. Avoid printing resolved private configuration; report results with redacted labels such as “login alias,” “compute alias,” and “active allocation.”

Store non-secret machine-specific settings in a local configuration outside the skill directory with permissions restricted to the user. Keep secrets in the platform's normal credential mechanisms, not in that configuration file.

## Route the task

- For first-time setup or migration, copy the supplied placeholder template, collect only missing non-secret values, and then follow the reference.
- For normal operation, use the configured login and compute aliases instead of expanding their underlying identities in commands or responses.
- Keep allocation and IDE automation on the local machine.
- Use `vista-open-all JOB_ID [cursor|code]` when every node of an existing allocation should open without any possibility of submitting another job.
- Keep Codex/tmux recovery on the remote compute node.
- For diagnosis, inspect state read-only first and avoid reproducing identifiers in the answer.

## Allocation rules

Vista currently documents `gg` (Grace–Grace CPU), `gh` (Grace–Hopper GPU), and `gh-dev` (Grace–Hopper development) partitions. Its published limits are 48 hours for `gg` and `gh`, and 2 hours for `gh-dev`. Treat these as documented examples, not guaranteed account access or permanent configuration: check the live account limits with `qlimits`, then populate the external `PARTITIONS`, `PARTITION_LIMITS`, and `PARTITION_NODE_LIMITS` values accordingly. Accept a short hours form and optionally an `HH:MM:SS` form. Accept an optional positive node count after the time; default to one node. A persistent allocation can use a noninteractive Slurm job that sleeps until its wall time expires.

The local wrapper should:

1. submit the explicitly requested allocation or reuse an active allocation only when its partition, wall time, and node count all match;
2. retain the numeric job ID only in process memory;
3. wait for that exact job to become running;
4. resolve its assigned nodelist without writing node names into the skill;
5. atomically update a private SSH include;
6. open the preferred IDE at the configured remote project directory.

When an IDE is requested, also open the loopback-only monitoring dashboard unless `TACC_VISTA_DASHBOARD_AUTO_OPEN=0`. Dashboard failure must warn without blocking an otherwise valid IDE connection.

Shorter wall times may improve backfill but never guarantee an immediate start. Do not switch partitions, shorten or extend a job, cancel a job, or submit an additional allocation without explicit user authorization. Resolve the exact target privately and verify the resulting state.

## SSH behavior

Use a stable login alias and a stable compute alias. The compute alias obtains its current hostname from a locally generated include and connects through the login alias with `ProxyJump`. Use OpenSSH multiplexing for the login connection when the site's policy permits it.

The IDE SSH target must always be the compute alias after its allocation reaches `RUNNING`. Never open the remote project on the login alias; the login node is used only for authentication, scheduler commands, and the `ProxyJump` transport to the assigned compute node.

`ControlPersist yes` has no configured expiry, but the master can still end after a reboot, process termination, socket removal, network or server disconnect, site policy action, or explicit closure. Losing the SSH master must not be described as cancelling a Slurm allocation; the two lifetimes are independent.

## Job dashboard

Install `vista-dashboard-open` locally and the dashboard server plus `dashboard-start.sh` on the login host. Run the server on a login node, bind it only to `127.0.0.1`, and expose it locally through an SSH forward on the existing login master. Never bind the dashboard to a public interface or write runtime job, node, account, username, or telemetry data into the skill directory.

The dashboard must provide:

- a home page for active jobs plus recent terminal history, with real Slurm estimated-start and priority data when available;
- a per-job page with real SVG line charts for CPU utilization, RSS/MaxRSS, virtual memory, cumulative CPU time, GPU utilization, GPU memory, temperature, power, and clocks;
- separate node/GPU series for multi-node GPU jobs, including Vista partitions that expose GPUs by node type without GPU GRES/TRES entries;
- per-node CPU utilization, used memory, memory percentage, and load curves sampled from each allocated node, with Slurm job-level CPU time and MaxRSS kept separately;
- zero-valued chart series when a metric or device is absent, rather than replacing charts with text;
- a bilingual Chinese/English toggle whose choice survives polling, reloads, and local-port changes;
- persistent open/closed state for job-detail controls across automatic polling;
- a two-step visual submission form in the homepage header for an existing Vista `sbatch` script;
- a guarded cancel button on active-job rows and detail pages;
- a bilingual common-command page with copy-only buttons and explicit local/login/compute execution locations.

Poll the homepage without launching per-job live sampling steps. On a running detail page, keep job-level CPU time and MaxRSS from `sstat`, and sample `/proc/stat`, `/proc/meminfo`, and `/proc/loadavg` once per allocated node for node-level CPU, memory, and load curves. Derive CPU utilization from adjacent cumulative tick samples in the browser. Query GPU metrics for jobs whose Slurm TRES data indicates a GPU allocation or whose configured GPU partition provides accelerators implicitly. Use short overlapping `srun` tasks, pass the job partition explicitly, prefix samples with the hostname, and key each series by node plus GPU index. Never run `nvidia-smi` for CPU-only jobs.

Treat only pending, running, configuring, completing, suspended, or stopped states as active. Put cancelled, failed, timed-out, out-of-memory, node-failed, preempted, and completed jobs in history. Merge terminal IDs still visible through `squeue` with accounting history so a never-started cancellation moves out of the active table immediately.

Explain that charts begin sampling when a job page is opened and keep recent samples in the browser; the dashboard cannot reconstruct telemetry that was never collected. The command-reference page remains copy-only.

The homepage submission form and active-job cancel button are the only scheduler mutations. For submission, require an existing readable absolute Vista script path, accept only structured optional overrides, validate partition/time/numeric/identifier fields, show a separate review step, then call `sbatch --parsable` with an argument list and no shell. Use a per-process request ID cache so a repeated confirmation cannot create a duplicate job. Do not upload, edit, discover, or persist script contents or form values.

For cancellation, require an exact typed Job ID confirmation, resolve the OS login user on the server, verify the exact job is active for that user through `squeue`, and invoke `scancel` with an argument list. Both endpoints must accept only POST with a per-process request token. Never accept a client-supplied user or expose the server outside loopback.

## Normal operation

Explain the post-install workflow with command locations made explicit:

1. On the local computer, optionally pre-establish the reusable login master with `ssh -O check LOGIN_ALIAS 2>/dev/null || ssh -MNf LOGIN_ALIAS`. This prompts for fresh authentication only when no usable master exists.
2. Still on the local computer, run `vista-allocate PARTITION HOURS NODES cursor` (or `code`, `cursor-all`, `code-all`, or `none`). Omit `NODES` for a one-node allocation; preserve the legacy `vista-allocate PARTITION HOURS EDITOR` form. Never tell the user to run this local wrapper from the login-node shell.
3. The wrapper submits or reuses the configured allocation, waits for that exact job, updates the base compute alias, creates allocation- and node-specific aliases in private local state, opens the dashboard through the login master, and opens the requested IDE window or windows.
4. In the IDE's compute-node terminal, optionally run `~/start.sh` for managed multi-window Codex recovery, or use `codex resume --all` manually.

Keep `vista-open-all JOB_ID [cursor|code]` separate from `vista-allocate`. It must accept only an explicit existing numeric Job ID, resolve that job, refresh its private per-node aliases, and open one window per node. It must never call an allocation submit helper or fall back to creating a job when the ID is missing, finished, or invalid. It may wait when that exact existing job is pending.

For a multi-node allocation, resolve the entire Slurm nodelist. Keep `cursor` and `code` as the safe default that opens one window on the first node. Support explicit `cursor-all` and `code-all` modes that open one window for each numbered node alias. Preserve earlier allocation-specific aliases so simultaneous allocations can keep separate IDE windows and reconnect without a later invocation redirecting them. Explain that several open terminals do not automatically coordinate a distributed workload or use every accelerator; use an allocation-aware launcher for that.

When launching Cursor, use its classic-window flag together with the remote folder URI. Current Cursor releases can otherwise route a command-line launch into the Agent/Glass landing window even though the Remote-SSH target is valid. This compatibility flag affects the window type; it does not disable Cursor's Agent features inside the IDE.

Also explain that pre-establishing the login master is optional because `vista-allocate` can trigger SSH authentication itself. An interactive `ssh LOGIN_ALIAS` may remain open, but the allocation wrapper still runs from a separate local terminal.

## Remote recovery

The remote recovery command must refuse login nodes and enter the configured shared project directory. Treat each managed Codex window as a separate named tmux session, or a separate GNU screen session when tmux is unavailable. Persist only a restricted active marker and exact Codex session ID for each window outside the skill directory.

On every `~/start.sh` invocation, recreate all active managed windows that are missing on the current node, then attach the requested window or the first active one. Allow new windows without replacing restored windows. A normal Codex exit or explicit `~/start.sh --close WINDOW` must clear that window's active marker; an SSH disconnect, IDE disconnect, abnormal process exit, or compute-node loss must leave it marked for recovery. Never infer that an unmanaged Codex process was intentionally closed.

Do not use `codex resume --last` for a multi-window workflow. Bind each managed window to its exact session ID. If no binding exists yet, show the all-session picker once and record the selected session from the running Codex process without reading conversation content. Existing unmanaged Codex processes require a one-time, explicit migration into named managed windows. Do not resume the same session concurrently on two compute nodes.

A new compute node requires a new tmux/screen server. Shared files and Codex session records remain available; running applications resume only through their own checkpoint mechanisms.

Treat `~/start.sh` as optional convenience, not a prerequisite for allocation, SSH jumping, or IDE access. When the compute allocation expires, explain that the old Codex processes and tmux/screen server end with the node. A user may instead enter the shared project directory on the next node and run `codex resume --all` manually. The managed launcher automates reconstructing multiple active windows; it does not restore process memory.

## Verification

Validate shell, Python, embedded JavaScript, and SSH configuration without echoing resolved identities. Test the login path, dashboard tunnel, and compute path separately. Verify the dashboard server is loopback-only, that the homepage does not run GPU sampling, and that control tests replace both `sbatch` and `scancel` with mocks. During a read-only diagnosis, do not submit, modify, or cancel scheduler jobs.
