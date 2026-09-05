---
name: tacc-vista-shareable
description: Configure or operate a standalone, privacy-preserving TACC Vista SSH, Slurm allocation, IDE jump, and remote Codex recovery workflow from placeholders. Use for portable setups that must not embed or disclose identifiers, paths, credentials, job data, or node data.
---

# TACC Vista Shareable

Build this standalone workflow on a user's machine:

```text
local login authentication -> remote Slurm allocation -> dynamic compute SSH alias -> IDE
compute-node shell -> named tmux/screen windows -> exact Codex sessions
```

Start from the placeholders in [references/configuration-template.md](references/configuration-template.md). Copy [assets/tacc-vista.env.example](assets/tacc-vista.env.example) outside the skill before replacing placeholders. Use [assets/ssh-config.example](assets/ssh-config.example) only as a merge template; never overwrite an existing SSH config wholesale.

## Executable setup

For a first-time installation, help the user fill the external configuration, then run `scripts/install.sh`. The installer creates the local SSH fragment and helper commands, installs the remote allocation/recovery helpers through the configured login alias, and leaves existing unrelated SSH settings intact. Run `scripts/doctor.sh --remote` afterward. Use `tests/smoke.sh` to validate the package itself without contacting Vista.

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
- Keep Codex/tmux recovery on the remote compute node.
- For diagnosis, inspect state read-only first and avoid reproducing identifiers in the answer.

## Allocation rules

Support the partitions and limits available to the current account; discover them from the scheduler instead of assuming another user's values. Accept a short hours form and optionally an `HH:MM:SS` form. A persistent allocation can use a noninteractive Slurm job that sleeps until its wall time expires.

The local wrapper should:

1. submit or intentionally reuse one configured allocation job;
2. retain the numeric job ID only in process memory;
3. wait for that exact job to become running;
4. resolve its assigned node without writing the node into the skill;
5. atomically update a private SSH include;
6. open the preferred IDE at the configured remote project directory.

Shorter wall times may improve backfill but never guarantee an immediate start. Do not switch partitions, shorten or extend a job, cancel a job, or submit an additional allocation without explicit user authorization. Resolve the exact target privately and verify the resulting state.

## SSH behavior

Use a stable login alias and a stable compute alias. The compute alias obtains its current hostname from a locally generated include and connects through the login alias with `ProxyJump`. Use OpenSSH multiplexing for the login connection when the site's policy permits it.

The IDE SSH target must always be the compute alias after its allocation reaches `RUNNING`. Never open the remote project on the login alias; the login node is used only for authentication, scheduler commands, and the `ProxyJump` transport to the assigned compute node.

`ControlPersist yes` has no configured expiry, but the master can still end after a reboot, process termination, socket removal, network or server disconnect, site policy action, or explicit closure. Losing the SSH master must not be described as cancelling a Slurm allocation; the two lifetimes are independent.

## Normal operation

Explain the post-install workflow with command locations made explicit:

1. On the local computer, optionally pre-establish the reusable login master with `ssh -O check LOGIN_ALIAS 2>/dev/null || ssh -MNf LOGIN_ALIAS`. This prompts for fresh authentication only when no usable master exists.
2. Still on the local computer, run `vista-allocate PARTITION HOURS cursor` (or `code`/`none`). Never tell the user to run this local wrapper from the login-node shell.
3. The wrapper submits or reuses the configured allocation, waits for that exact job, updates the compute alias, and opens the configured project directory in an IDE whose SSH target is the compute alias.
4. In the IDE's compute-node terminal, optionally run `~/start.sh` for managed multi-window Codex recovery, or use `codex resume --all` manually.

Also explain that pre-establishing the login master is optional because `vista-allocate` can trigger SSH authentication itself. An interactive `ssh LOGIN_ALIAS` may remain open, but the allocation wrapper still runs from a separate local terminal.

## Remote recovery

The remote recovery command must refuse login nodes and enter the configured shared project directory. Treat each managed Codex window as a separate named tmux session, or a separate GNU screen session when tmux is unavailable. Persist only a restricted active marker and exact Codex session ID for each window outside the skill directory.

On every `~/start.sh` invocation, recreate all active managed windows that are missing on the current node, then attach the requested window or the first active one. Allow new windows without replacing restored windows. A normal Codex exit or explicit `~/start.sh --close WINDOW` must clear that window's active marker; an SSH disconnect, IDE disconnect, abnormal process exit, or compute-node loss must leave it marked for recovery. Never infer that an unmanaged Codex process was intentionally closed.

Do not use `codex resume --last` for a multi-window workflow. Bind each managed window to its exact session ID. If no binding exists yet, show the all-session picker once and record the selected session from the running Codex process without reading conversation content. Existing unmanaged Codex processes require a one-time, explicit migration into named managed windows. Do not resume the same session concurrently on two compute nodes.

A new compute node requires a new tmux/screen server. Shared files and Codex session records remain available; running applications resume only through their own checkpoint mechanisms.

Treat `~/start.sh` as optional convenience, not a prerequisite for allocation, SSH jumping, or IDE access. When the compute allocation expires, explain that the old Codex processes and tmux/screen server end with the node. A user may instead enter the shared project directory on the next node and run `codex resume --all` manually. The managed launcher automates reconstructing multiple active windows; it does not restore process memory.

## Verification

Validate shell syntax and SSH configuration without echoing resolved identities. Test the login path and compute path separately. During a read-only diagnosis, do not submit, modify, or cancel scheduler jobs.
