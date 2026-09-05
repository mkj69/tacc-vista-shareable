# TACC Vista Shareable Skill

A privacy-preserving Codex skill for configuring a TACC Vista workflow that authenticates through a login host, requests a Slurm allocation, updates a stable compute-node SSH alias, connects Cursor or VS Code to the assigned compute node, and resumes Codex inside tmux or GNU screen.

The repository contains placeholders only. It does not include usernames, account identifiers, endpoints, personal paths, node names, job IDs, credentials, or session data.

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
local computer:  vista-allocate [partition] [hours] [IDE]
compute node:    ~/start.sh
```

The local side keeps stable login and compute aliases, reuses a live authenticated login connection with OpenSSH `ControlMaster`/`ControlPersist`, requests and tracks one exact Slurm allocation, updates the private node mapping, and opens the chosen IDE on that compute node at the configured directory. The login node is only the authentication, scheduler, and SSH jump layer; the IDE workspace and Codex processes run on the allocated compute node. The remote side reconstructs the active managed Codex windows from shared state.

The skill never stores, predicts, submits, or bypasses a password or multifactor token. The user enters the token directly into SSH when fresh authentication is required. Reuse lasts only while the SSH master remains alive and the site permits it; a reboot, network/server disconnect, explicit master shutdown, or site policy can require a new token.

## What the workflow does

```text
LOCAL COMPUTER
┌──────────────────────────────────────────────────────────────┐
│ vista-allocate [partition] [hours] [cursor|code|none]        │
│   │                                                          │
│   ├─ reuse the SSH login master when it is still alive       │
│   ├─ submit one persistent Slurm allocation                  │
│   ├─ wait for that exact job to reach RUNNING                │
│   ├─ write its node into a private dynamic SSH include       │
│   └─ open Cursor / VS Code through the stable compute alias  │
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
vista-allocate [partition] [hours] [cursor|code|none]
```

Once connected to a compute node, use the managed Codex windows:

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
scripts/remote/submit-node.sh
scripts/remote/resolve-node.sh
scripts/remote/codex-start.sh
scripts/remote/run-codex.sh
scripts/remote/start.sh
tests/smoke.sh
```

Before publishing a change, run the Codex skill validator and scan the repository for real identifiers, absolute personal paths, credentials, job data, and session artifacts.
