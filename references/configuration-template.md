# Configuration template

Every machine-specific value starts as a placeholder and is filled only in a user-restricted configuration outside the skill directory.

## Placeholder map

| Placeholder | Value supplied by the installer |
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
| `<DEFAULT_PARTITION>` | Partition chosen by the user |
| `<DEFAULT_HOURS>` | Requested default wall time in hours |
| `<PREFERRED_IDE>` | `cursor`, `code`, or `none` |
| `<LOCAL_NODE_INCLUDE>` | User-restricted file containing the current compute hostname |
| `<LOCAL_CONTROL_PATH>` | User-restricted OpenSSH control-socket pattern |

Do not substitute a guessed value. Ask for a missing non-secret value or derive it from existing local configuration with the user's permission. Never ask for a password, token code, private-key body, or saved session files.

## Configuration file

Copy `assets/tacc-vista.env.example` to a user-restricted configuration location outside the cloned skill/repository. Replace every angle-bracket placeholder there. Set file permissions so only the user can read and write it. Do not commit the populated file.

The example intentionally contains no defaults that could be mistaken for another person's account. Partition names and limits must be checked against the current Vista scheduler and account rather than copied from someone else's setup.

## SSH configuration

Treat `assets/ssh-config.example` as a fragment. Replace its placeholders from the external configuration, then merge only the required blocks into the user's SSH config. Preserve all unrelated hosts and directives.

The generated node include starts with a non-routable placeholder hostname. After Slurm assigns a node, the node updater atomically replaces that one include. Do not write assigned node names into this skill or repository.

## Local helper roles

- Allocation wrapper: read the external configuration, validate partition/time, call the remote submit helper, retain the job ID in process memory, wait for the exact job, update the node include, and launch the selected IDE.
- Node updater: distinguish pending/configuring from terminal failure and stop if the job disappears.
- IDE integration: use the stable compute alias, not a hard-coded node name.

## Remote helper roles

- Submit helper: read the scheduler account, job label, partition limits, and output location from external configuration; submit one node and one task; return a clean numeric ID even if the site prints a banner.
- Resolver: accept an exact job ID, return only a running node, and use distinct statuses for “wait” and “job unavailable.”
- Recovery launcher: reject login nodes, enter `<REMOTE_PROJECT_DIR>`, create or attach `<TMUX_SESSION_NAME>`, and run `codex resume --last` only when saved sessions exist.

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
