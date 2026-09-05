---
name: tacc-vista-shareable
description: Configure or operate a standalone, privacy-preserving TACC Vista SSH, Slurm allocation, IDE jump, and remote Codex recovery workflow from placeholders. Use for portable setups that must not embed or disclose identifiers, paths, credentials, job data, or node data.
---

# TACC Vista Shareable

Build this standalone workflow on a user's machine:

```text
local login authentication -> remote Slurm allocation -> dynamic compute SSH alias -> IDE
compute-node shell -> tmux -> Codex resume
```

Start from the placeholders in [references/configuration-template.md](references/configuration-template.md). Copy [assets/tacc-vista.env.example](assets/tacc-vista.env.example) outside the skill before replacing placeholders. Use [assets/ssh-config.example](assets/ssh-config.example) only as a merge template; never overwrite an existing SSH config wholesale.

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

`ControlPersist yes` has no configured expiry, but the master can still end after a reboot, process termination, socket removal, network or server disconnect, site policy action, or explicit closure. Losing the SSH master must not be described as cancelling a Slurm allocation; the two lifetimes are independent.

## Remote recovery

The remote recovery command must refuse login nodes, enter the configured shared project directory, create or attach a tmux session, and use `codex resume --last` only when saved sessions exist. A new compute node requires a new tmux server. Shared files remain available; running applications resume only through their own checkpoint mechanisms.

## Verification

Validate shell syntax and SSH configuration without echoing resolved identities. Test the login path and compute path separately. During a read-only diagnosis, do not submit, modify, or cancel scheduler jobs.
