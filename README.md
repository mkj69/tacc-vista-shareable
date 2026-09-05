# TACC Vista Shareable Skill

A privacy-preserving Codex skill for configuring a TACC Vista workflow that authenticates through a login host, requests a Slurm allocation, opens a CPU/GPU job dashboard, updates a stable compute-node SSH alias, connects Cursor or VS Code to the assigned compute node, and resumes Codex inside tmux or GNU screen.

The repository contains placeholders only. It does not include usernames, account identifiers, endpoints, personal paths, node names, job IDs, credentials, or session data.

## News

### 2026-09-05 — CPU/GPU job dashboard

The installer now adds `vista-dashboard-open` and a loopback-only Vista dashboard. When an allocation reaches `RUNNING`, the wrapper opens the dashboard and then the requested IDE. The dashboard includes:

- a home page for active and recently finished jobs;
- estimated start time, priority factors, wait reasons, resources, paths, and Slurm details;
- one detail page per job with real SVG line charts—not text charts—for CPU utilization, RSS/MaxRSS, virtual memory, cumulative CPU time, GPU utilization, GPU memory, temperature, power, and clocks;
- zero lines when a metric is unused or unavailable;
- Chinese/English switching, persistent expanded details, and five-second polling;
- a common-command page with copy-only examples for local SSH, allocation, queue inspection, submission, cancellation, monitoring, and Codex recovery.

Charts begin sampling when a job detail page opens. Recent samples stay in that browser, so telemetry that was never sampled cannot be reconstructed later. The dashboard runs on the login node because its GPU inspection path launches a short overlapping step inside an existing running allocation; the IDE still connects to the compute node. The server binds only to remote loopback and is reached through the reusable SSH login connection.

### 2026-09-05 — Multi-node allocation support

`vista-allocate` now accepts an optional node count between the wall time and IDE arguments:

```bash
vista-allocate gh 10 4
vista-allocate gh 10 4 code
vista-allocate gh 10 4 none
```

The first command requests four `gh` nodes for ten hours and opens Cursor on the first assigned node. On Vista's current Grace–Hopper layout, four `gh` nodes correspond to four GPUs. The remaining nodes stay in the same Slurm allocation and must be used through an allocation-aware distributed launcher; opening an IDE on the first node does not automatically run work on all four GPUs.

Each `vista-allocate` invocation opens exactly one IDE window for its allocation. The wrapper creates a private allocation-specific SSH alias, so a `gh` window and a later `gg` window remain connected to their own jobs instead of both following whichever node the shared base alias most recently selected.

Existing one-node commands remain compatible. A numeric third argument means node count, while `cursor`, `code`, or `none` as the third argument retains the legacy editor selection:

```bash
vista-allocate gh 10          # one node, preferred IDE
vista-allocate gh 10 code     # one node, VS Code
```

Active allocations are now reused only when partition, requested wall time, and node count all match. This prevents a multi-node request from silently attaching to an older single-node allocation. Installations must add `PARTITION_NODE_LIMITS` to their external configuration and rerun `scripts/install.sh` to receive the updated helpers.

## Why this skill is useful

A normal interactive Vista workflow crosses several separate layers. Without a wrapper, the user may need to remember and repeat the whole sequence every time an allocation or compute node changes:

```text
open a terminal
  -> SSH to a login node
  -> enter the current multifactor token
  -> submit an allocation
  -> repeatedly inspect the Slurm queue
  -> discover the assigned compute-node hostname
  -> update or reconstruct the SSH jump command
  -> connect the IDE to that compute node, using the login node only as a jump host
  -> possibly authenticate again for a new SSH connection
  -> reopen the correct remote project directory
  -> recreate terminal sessions and find the right Codex conversations
```

Opening another IDE window or remote folder can also start another SSH connection, which may lead to another authentication prompt when no reusable connection exists. Compute-node hostnames change between allocations, while tmux/screen servers disappear with the old node. That makes an otherwise routine transition surprisingly easy to get wrong.

This skill turns those moving pieces into two stable entry points:

```text
local computer:  vista-allocate [partition] [hours] [nodes] [IDE]
compute node:    ~/start.sh
```

The local side keeps stable login and compute aliases, reuses a live authenticated login connection with OpenSSH `ControlMaster`/`ControlPersist`, requests and tracks one exact Slurm allocation, updates the private node mapping, and opens the chosen IDE on that compute node at the configured directory. The login node is only the authentication, scheduler, and SSH jump layer; the IDE workspace and Codex processes run on the allocated compute node. The remote side reconstructs the active managed Codex windows from shared state.

The skill never stores, predicts, submits, or bypasses a password or multifactor token. The user enters the token directly into SSH when fresh authentication is required. Reuse lasts only while the SSH master remains alive and the site permits it; a reboot, network/server disconnect, explicit master shutdown, or site policy can require a new token.

## What the workflow does

```text
LOCAL COMPUTER
┌──────────────────────────────────────────────────────────────┐
│ vista-allocate [partition] [hours] [nodes] [IDE]             │
│   │                                                          │
│   ├─ reuse the SSH login master when it is still alive       │
│   ├─ submit one persistent Slurm allocation                  │
│   ├─ wait for that exact job to reach RUNNING                │
│   ├─ create a private allocation-specific SSH alias          │
│   ├─ open the job dashboard through the login SSH master     │
│   └─ open one IDE window through that allocation alias       │
└───────────────────────────┬──────────────────────────────────┘
                            │ ProxyJump through login alias
                            ▼
VISTA COMPUTE NODE
┌──────────────────────────────────────────────────────────────┐
│ ~/start.sh                                                   │
│   ├─ read the shared active-window registry                  │
│   ├─ recreate every missing named tmux/screen session        │
│   ├─ resume each window's exact Codex session ID             │
│   └─ attach the requested window (or the first active one)    │
│                                                              │
│ ~/start.sh --new WINDOW     add an independent Codex window  │
│ ~/start.sh --close WINDOW   close it and stop future restore │
└───────────────────────────┬──────────────────────────────────┘
                            │ allocation ends / SSH disconnects
                            ▼
SHARED STORAGE
┌──────────────────────────────────────────────────────────────┐
│ project files + Codex sessions + active-window registry      │
│ survive; tmux/screen and ordinary running processes do not   │
└───────────────────────────┬──────────────────────────────────┘
                            │ connect to the next compute node
                            └──────────────► run ~/start.sh again
```

An SSH or IDE disconnect does not cancel the Slurm allocation. A new node gets a new tmux/screen server, while the helper reconstructs the managed Codex windows from shared state. Training or Python processes require their own checkpoint support.

## When the compute-node time expires

When the Slurm wall time ends, access to that compute node ends and its running Codex processes and tmux/screen server disappear. An SSH or IDE connection cannot keep them alive past the allocation. This is different from briefly disconnecting SSH while the allocation is still running: tmux/screen can survive the brief disconnect, but it cannot survive the loss of the compute node itself.

If `CODEX_HOME` and the project are on shared storage, the saved Codex conversations and project files are still available from the next compute node. The user can always recover a conversation manually without using the managed-window feature:

```bash
cd <REMOTE_PROJECT_DIR>
codex resume --all
```

The user can then choose the previous conversation from the Codex picker. This works one conversation at a time and is a perfectly valid option.

`~/start.sh` is an optional convenience layer. It remembers which managed Codex windows were still active, recreates their new tmux/screen containers on the replacement compute node, resumes their exact saved sessions, and attaches one of them. Users who prefer manual recovery can skip `start.sh`; node allocation, SSH jumping, and IDE connection still work independently.

Neither method restores the old compute node's process memory. Unsaved in-memory work, running shells, Python programs, and training processes require application-level checkpoints or saved files.

## Install

Clone the repository into the Codex skills directory:

```bash
git clone <REPOSITORY_URL> "${CODEX_HOME:-$HOME/.codex}/skills/tacc-vista-shareable"
```

Then invoke it with:

```text
$tacc-vista-shareable configure a Vista allocation and IDE workflow for this machine
```

## Configure and install

Machine-specific values are never filled inside the repository:

1. Copy `assets/tacc-vista.env.example` to a user-restricted configuration location outside the repository.
2. Replace each angle-bracket placeholder with that user's own non-secret setting.
3. Install the workflow and run the read-only doctor:

   ```bash
   chmod 600 "${XDG_CONFIG_HOME:-$HOME/.config}/tacc-vista/config"
   ./scripts/install.sh
   ./scripts/doctor.sh --remote
   ```

4. Let SSH request passwords or multifactor tokens directly; never store them in the configuration.

See `references/configuration-template.md` for the placeholder contract and safe setup sequence.

The installer does not submit a Slurm job. After installation, the user explicitly starts an allocation with:

```bash
vista-allocate [partition] [hours] [nodes] [cursor|code|none]
```

The dashboard opens automatically when an IDE is requested. It can also be opened independently:

```bash
vista-dashboard-open
```

### How to read the placeholders and command examples

The names below are documentation stand-ins, not universal TACC commands or values that should be typed literally:

| Text in this README | What it means |
|---|---|
| `your-login-alias` | The short local SSH name chosen for `LOGIN_ALIAS`. It represents the Vista login host after installation; it is not necessarily a username or hostname. Replace it with the configured value before running a command. |
| `your-compute-alias` | The stable local SSH name chosen for `COMPUTE_ALIAS`. The installer makes it jump through the login alias and point to whichever compute node the current Slurm allocation receives. Users do not manually replace it with the changing node hostname. |
| `<REMOTE_PROJECT_DIR>` | The user's absolute shared directory on Vista. Angle brackets mark a value that must be replaced in the external configuration; do not type the brackets. The IDE opens this directory on the compute node. |
| `<REPOSITORY_URL>` | The clone URL of this public repository. Replace the entire angle-bracket expression, including the brackets. |
| `[partition]` | A command argument such as `gg`, `gh`, or `gh-dev`, subject to the user's account access. Square brackets in a command synopsis describe an argument; they are not typed. |
| `[hours]` | Requested Slurm wall time in hours, for example `6`, within the selected partition's limit. |
| `[nodes]` | Optional positive node count. Omit it for one node. A numeric third argument is interpreted as nodes; configured per-partition limits are enforced. |
| `[cursor\|code\|none]` | Whether to open Cursor, open VS Code, or only prepare the compute SSH alias. |
| `WINDOW` | A user-chosen label for one managed Codex window, such as `paper` or `analysis`; it is not an operating-system window ID. |

An SSH alias is simply a convenient local name defined in `~/.ssh/config`. For example, after `LOGIN_ALIAS` has been configured, `ssh your-login-alias` means “replace `your-login-alias` with that chosen short name and let SSH read the real username, login endpoint, key path, and connection settings from its configuration.” The populated machine-specific values live outside this repository so they cannot be exposed by sharing the skill.

## Everyday operation after installation

The following commands are run on the **local computer**, not inside the Vista login node.

### 1. Ensure that the reusable login connection exists

Replace the literal text `your-login-alias` with the value of `LOGIN_ALIAS` chosen in the external configuration:

```bash
ssh -O check your-login-alias 2>/dev/null || ssh -MNf your-login-alias
```

If the SSH master is already alive, the check succeeds and no new connection is opened. Otherwise, SSH starts a background master and asks the user for the password or multifactor token required by the site. The command then returns to the local shell, where `vista-allocate` is available.

This preparation is recommended but optional. Running `vista-allocate` without it can establish the SSH connection itself and display the same authentication prompt. A user may also run an interactive `ssh your-login-alias`, but then `vista-allocate` must be run from a second **local** terminal—not from the login-node shell.

#### When the reusable SSH connection ends

`ControlPersist yes` means that OpenSSH does not apply a configured idle-expiration time to the background master. It does **not** make the underlying TCP connection permanent or automatically recreate it after a failure. The master can still end when:

- the local computer sleeps, closes its lid, logs out, restarts, or shuts down;
- Wi-Fi, VPN, network interfaces, IP addresses, or upstream routing change;
- a router or NAT gateway removes the idle TCP connection;
- the local SSH process is terminated or its control socket is removed;
- the login server closes the connection, restarts, or enforces a site policy;
- the user explicitly runs `ssh -O exit your-login-alias`.

Short network interruptions may survive, but this is not guaranteed. SSH keepalives help detect a broken connection; they cannot keep the network active while a computer is asleep or bypass fresh multifactor authentication.

Check the master after waking the computer with:

```bash
ssh -O check your-login-alias
```

If it is gone, recreate it and enter a fresh token when prompted:

```bash
ssh -MNf your-login-alias
```

This SSH lifetime is independent of the Slurm allocation. Losing the master does not cancel a running compute-node allocation. Running `vista-allocate` again can authenticate, rediscover or reuse the active allocation, refresh the compute alias, and reopen the IDE.

### 2. Request a compute node and open the IDE

Vista currently documents three primary partitions:

| Partition | Node type | Published maximum wall time |
|---|---|---:|
| `gg` | Grace–Grace CPU nodes | 48 hours |
| `gh` | Grace–Hopper GPU nodes | 48 hours |
| `gh-dev` | Grace–Hopper development queue | 2 hours |

For example:

```bash
vista-allocate gg 6 cursor
vista-allocate gh 6 cursor
vista-allocate gh-dev 2 cursor
vista-allocate gh 10 4 cursor
```

Only use a partition available to the current TACC account. Queue availability and limits can change, and TACC notes that its documentation table may lag behind the live scheduler configuration. Check the current account's real-time limits from the local computer with:

```bash
ssh your-login-alias qlimits
```

Then make sure the external `PARTITIONS`, `PARTITION_LIMITS`, and `PARTITION_NODE_LIMITS` settings match those results. See the [official TACC Vista queue documentation](https://docs.tacc.utexas.edu/hpc/vista/running/) for the current published definitions.

The final argument controls the launch:

- `cursor` opens Cursor.
- `code` opens VS Code.
- `none` prepares the compute SSH alias without opening an IDE.

For current Cursor releases, the launcher requests a classic IDE window before opening the Remote-SSH folder. This prevents Cursor from showing its standalone Agent/Glass landing page in place of the file editor. Cursor's Agent tools remain available inside the IDE.

The number following the partition is the requested number of hours. An optional number after it is the requested node count; omitting it requests one node. The command performs the remaining work:

```text
submit or reuse the configured Slurm allocation
  -> wait for that exact job to reach RUNNING
  -> discover its assigned compute node
  -> update the private compute SSH alias
  -> open the loopback-only CPU/GPU dashboard through the login host
  -> connect the IDE to the compute node through ProxyJump
  -> open the configured remote project directory
```

If the allocation is pending, leave the local command running; it waits until the node is ready. There is no need to copy the job ID, repeatedly run `squeue`, edit the node hostname, reconnect the IDE manually, or browse to the project directory again.

After a successful launch, the IDE's remote terminal is running on the **compute node**. The login node remains only the authentication, scheduler, and jump layer.

For each allocation, the wrapper creates an allocation-specific alias and points it to the first hostname in Slurm's assigned nodelist. It opens one IDE window through that alias. The other nodes are reserved by the same allocation, but ordinary commands typed into the IDE terminal run only on the first node. Use the site's supported distributed launcher and the allocation's job ID when intentionally starting work across all allocated nodes. Allocation-specific aliases remain separate, so opening another allocation does not redirect an earlier `gh` or `gg` window.

### 3. Optionally start or restore Codex on the compute node

From the terminal inside the remote IDE:

```bash
~/start.sh
```

This optional command restores the managed Codex windows described below. Users who do not want managed recovery can instead start Codex normally or run `codex resume --all` themselves.

### 4. Reconnect while the allocation is still active

Running the same `vista-allocate` command again reuses the matching active allocation, refreshes the compute-node mapping, and opens another IDE window. To open only a terminal connection after the mapping is ready, use the configured compute alias:

```bash
ssh your-compute-alias
```

If the reusable login master has ended, SSH asks for fresh authentication. This does not mean the Slurm allocation was cancelled; SSH and Slurm have independent lifetimes.

## Managed Codex window commands

Once connected to a compute node, use these optional managed Codex commands:

```bash
~/start.sh --new paper       # create a new managed window
~/start.sh experiments       # attach it, or choose a saved session on first use
~/start.sh --list            # show managed-window state
~/start.sh --close paper     # close it and exclude it from later recovery
~/start.sh                   # restore all active windows, then attach one
```

Each named window gets its own tmux session, or its own GNU screen session when tmux is unavailable. While Codex runs, the helper records that window's exact session ID in user-restricted state outside the repository. A normal Codex exit removes its active marker. An SSH/IDE disconnect or compute-node loss leaves the marker intact, so `~/start.sh` on the next node recreates every still-active managed window before attaching one.

Existing Codex processes that were started outside this helper require a one-time migration: exit each old process normally, run `~/start.sh WINDOW`, and select its saved session. Do not resume the same Codex session concurrently on two nodes.

Run the isolated package test without connecting to Vista:

```bash
./tests/smoke.sh
```

`doctor.sh` is read-only. It checks the external configuration, required local tools, generated SSH aliases, installed helpers, and—when `--remote` is supplied—the remote helper syntax and required command availability. It never submits or cancels a job.

## Dashboard behavior

The homepage intentionally avoids launching `nvidia-smi` for every job. Click **View CPU/GPU charts** beside an active or historical job to open its detail page. CPU and memory come from Slurm `sstat`. CPU utilization is calculated from the change in cumulative CPU time between samples and normalized by the allocated CPU count. GPU sampling runs only for a running job whose TRES data reports a GPU allocation; CPU-only jobs still display zero-valued GPU charts.

Automatic polling updates values without collapsing opened details or changing the selected language. The language preference is stored in both browser storage and a cookie so it also survives a change between local forwarding ports. A cancelled job is removed from the active table and merged into recent terminal history, including a never-started cancellation that Slurm's time-range accounting query may otherwise omit.

## Repository contents

```text
SKILL.md
agents/openai.yaml
assets/tacc-vista.env.example
assets/ssh-config.example
references/configuration-template.md
scripts/install.sh
scripts/doctor.sh
scripts/common.sh
scripts/local/vista-allocate
scripts/local/vista-node-update.sh
scripts/local/vista-dashboard-open
scripts/remote/submit-node.sh
scripts/remote/resolve-node.sh
scripts/remote/codex-start.sh
scripts/remote/run-codex.sh
scripts/remote/start.sh
scripts/remote/dashboard-start.sh
scripts/dashboard/vista_job_dashboard.py
tests/smoke.sh
```

Before publishing a change, run the Codex skill validator and scan the repository for real identifiers, absolute personal paths, credentials, job data, and session artifacts.
