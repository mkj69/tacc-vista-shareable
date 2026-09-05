# Configuration template

Every machine-specific value starts as a placeholder and is filled only in a user-restricted configuration outside the skill directory.

## Placeholder map

| Placeholder | Meaning |
|---|---|
| `<LOGIN_ALIAS>` | Stable local SSH alias for the Vista login host |
| `<COMPUTE_ALIAS>` | Stable local SSH alias for the allocated compute node |
| `<VISTA_LOGIN_HOST>` | Login endpoint from the site's official documentation |
| `<TACC_USERNAME>` | User's own TACC username |
| `<SSH_KEY_PATH>` | User-selected local SSH private-key path; never its contents |
| `<SCHEDULER_ACCOUNT>` | Slurm allocation/project identifier |
| `<REMOTE_PROJECT_DIR>` | Shared remote directory to open in the IDE |
| `<ALLOCATION_JOB_NAME>` | Label used to recognize the persistent allocation job |
| `<TMUX_SESSION_NAME>` | Name for the remote Codex tmux session |
| `<PARTITION_LIST>` | Comma-separated allowed partitions |
| `<PARTITION_LIMITS>` | Comma-separated `partition:max-hours` mappings |
| `<PARTITION_NODE_LIMITS>` | Comma-separated `partition:max-nodes-per-job` mappings |
| `<DEFAULT_PARTITION>` | Partition chosen by the user |
| `<DEFAULT_HOURS>` | Requested default wall time in hours |
| `<PREFERRED_IDE>` | `cursor`, `code`, or `none` |
| `<LOGIN_NODE_PATTERN>` | Shell glob matching the site's login-node hostnames |
| `<LOCAL_NODE_INCLUDE>` | Installer-generated, user-restricted file containing the current compute hostname |
| `<LOCAL_CONTROL_PATH>` | Installer-generated, user-restricted OpenSSH control-socket pattern |

Do not substitute a guessed value. Ask for a missing non-secret value or derive it from existing local configuration with the user's permission. Never ask for a password, token code, private-key body, or saved session files.

## Configuration file

Copy `assets/tacc-vista.env.example` to `${XDG_CONFIG_HOME:-$HOME/.config}/tacc-vista/config` outside the cloned skill/repository. Replace every angle-bracket placeholder there. Set file permissions so only the user can read and write it. Do not commit the populated file.

The example intentionally contains no defaults that could be mistaken for another person's account. Partition names and limits must be checked against the current Vista scheduler and account rather than copied from someone else's setup.

After filling the configuration, run `scripts/install.sh`. It installs the local helpers, generates the SSH fragment, connects through the configured login alias, and installs the remote helpers. Run `scripts/doctor.sh --remote` afterward for a read-only verification. Use `scripts/install.sh --local-only` only when intentionally deferring remote setup.

## SSH configuration

Treat `assets/ssh-config.example` as a fragment. Replace its placeholders from the external configuration, then merge only the required blocks into the user's SSH config. Preserve all unrelated hosts and directives.

The generated node include starts with a non-routable placeholder hostname. After Slurm assigns a node, the node updater atomically replaces that one include. Do not write assigned node names into this skill or repository.

## Local helper roles

- Existing-allocation opener: accept an explicit numeric Job ID, refresh only that job's per-node aliases, and open one IDE window per node without any submission fallback.
- Allocation wrapper: read the external configuration, validate partition/time/node count, call the remote submit helper, retain the job ID in process memory, wait for the exact job, update the node include, and launch the selected IDE.
- Node updater: distinguish pending/configuring from terminal failure, stop if the job disappears, and write the latest base alias plus persistent allocation- and node-specific aliases into private local SSH state.
- IDE integration: default to one window through the primary allocation alias; support explicit `cursor-all`/`code-all` modes that open one window per allocated node. Preserve aliases for other active allocations so their windows do not get redirected.
- Dashboard opener: start the loopback-only service on the login node, reuse the authenticated login master for a local port forward, open the browser, and keep dashboard failure non-fatal to IDE launch.

## Remote helper roles

- Submit helper: read the scheduler account, job label, partition limits, and output location from external configuration; submit the requested node count with one task per node; return a clean numeric ID even if the site prints a banner. Reuse only an allocation whose partition, wall time, and node count all match.
- Resolver: accept an exact job ID, return only a running node, and use distinct statuses for “wait” and “job unavailable.”
- Recovery launcher: reject login nodes, enter `<REMOTE_PROJECT_DIR>`, create one named tmux/screen session per managed Codex window, bind it to an exact Codex session ID, and restore every window whose active marker survived the previous node.
- Dashboard service: run only on a login node and bind to loopback. Keep home/detail queries read-only and launch short overlapping GPU inspection steps only for running GPU allocations. For visual submission, require a readable absolute existing script, validate structured optional overrides, show review and confirmation steps, and call `sbatch --parsable` with an argument list plus an idempotency request ID. For cancellation, require the exact typed Job ID, current OS user ownership, and an exact active `squeue` match before calling `scancel` with an argument list. Protect both POST endpoints with the per-process request token and never persist form values or script contents.

## Managed Codex window lifecycle

- `~/start.sh --new WINDOW` creates a new managed window without replacing restored ones.
- `~/start.sh WINDOW` attaches that window. On first migration it shows the all-session picker, then records the selected session ID.
- `~/start.sh` recreates all active managed windows missing on the current node and attaches one.
- `~/start.sh --list` reports whether each saved window is closed, marked active, or running locally.
- A normal Codex exit or `~/start.sh --close WINDOW` removes the active marker, so the next node does not restore it.
- Disconnecting SSH or the IDE, or losing the compute node, leaves active markers intact.

This lifecycle applies only after a Codex window has been launched or migrated through the helper. To migrate an existing unmanaged window, exit that Codex process, run `~/start.sh WINDOW`, and select its saved session. Never run the same session concurrently on old and new nodes.

## Safe setup sequence

1. Inspect existing SSH, shell, editor, and remote files without changing them.
2. Copy the placeholder configuration outside the skill directory.
3. Fill only non-secret values and restrict file permissions.
4. Back up any existing file that needs a substantial rewrite.
5. Merge the SSH fragment and install the smallest required helpers.
6. Run shell syntax checks.
7. Validate SSH aliases without printing expanded usernames, paths, or hostnames.
8. Test login connectivity before compute connectivity.
9. Submit a scheduler job only when the user explicitly asks for a live allocation.

## Sanitization before sharing

Scan every repository file and archive for real names, usernames, emails, numeric account/allocation identifiers, absolute home or scratch paths, hostnames, node names, job IDs, key paths, tokens, and session artifacts. Only angle-bracket placeholders may represent machine-specific values. Re-run the skill validator after sanitization.
