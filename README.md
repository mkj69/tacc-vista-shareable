# TACC Vista Shareable Skill

A privacy-preserving Codex skill for configuring a TACC Vista workflow that connects through a login host, requests a Slurm allocation, updates a stable compute-node SSH alias, opens Cursor or VS Code, and resumes Codex inside tmux.

The repository contains placeholders only. It does not include usernames, account identifiers, endpoints, personal paths, node names, job IDs, credentials, or session data.

## Install

Clone the repository into the Codex skills directory:

```bash
git clone <REPOSITORY_URL> "${CODEX_HOME:-$HOME/.codex}/skills/tacc-vista-shareable"
```

Then invoke it with:

```text
$tacc-vista-shareable configure a Vista allocation and IDE workflow for this machine
```

## Configure

The skill guides Codex through the setup. Machine-specific values are never filled inside the repository:

1. Copy `assets/tacc-vista.env.example` to a user-restricted configuration location outside the repository.
2. Replace each angle-bracket placeholder with that user's own non-secret setting.
3. Use `assets/ssh-config.example` as a fragment to merge into the existing SSH configuration.
4. Let SSH request passwords or multifactor tokens directly; never store them in the configuration.

See `references/configuration-template.md` for the placeholder contract and safe setup sequence.

## Repository contents

```text
SKILL.md
agents/openai.yaml
assets/tacc-vista.env.example
assets/ssh-config.example
references/configuration-template.md
```

Before publishing a change, run the Codex skill validator and scan the repository for real identifiers, absolute personal paths, credentials, job data, and session artifacts.
