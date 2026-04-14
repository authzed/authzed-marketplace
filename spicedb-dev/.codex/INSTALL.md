# Installing spicedb-dev for Codex

Enable spicedb-dev skills in Codex via native skill discovery. Clone and symlink.

## Prerequisites

- Git
- OpenAI Codex CLI

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/authzed/authzed-marketplace.git ~/.codex/authzed-marketplace
   ```

2. **Create the skills symlink:**
   ```bash
   mkdir -p ~/.agents/skills
   ln -s ~/.codex/authzed-marketplace/spicedb-dev/skills ~/.agents/skills/spicedb-dev
   ```

   **Windows (PowerShell):**
   ```powershell
   New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.agents\skills"
   cmd /c mklink /J "$env:USERPROFILE\.agents\skills\spicedb-dev" "$env:USERPROFILE\.codex\authzed-marketplace\spicedb-dev\skills"
   ```

3. **Restart Codex** (quit and relaunch the CLI) to discover the skills.

4. **(Optional) Enable multi-agent for subagent dispatch:**

   Some commands dispatch autonomous agents (schema-validator, checkpoint-identifier).
   To enable this, add to your Codex config (`~/.codex/config.toml`):

   ```toml
   [features]
   multi_agent = true
   ```

   Without this, agents run inline in the current session (functional but less isolated).

## How It Works

Codex has native skill discovery -- it scans `~/.agents/skills/` at startup, parses
SKILL.md frontmatter, and loads skills on demand. The symlink makes the plugin's four
skills visible:

- `authorization-planner` -- entry point, routes to the right command
- `spicedb-schema-design` -- schema patterns and anti-patterns
- `spicedb-best-practices` -- client code, consistency, performance
- `authorization-testing` -- test fixtures and scenarios

Commands (`commands/*.md`) and agents (`agents/*.md`) are loaded by skills when needed --
they do not require separate discovery.

## Usage

Skills are discovered automatically. Codex activates them when:
- You mention a skill by name (e.g., "use the authorization planner")
- The task matches a skill's description (e.g., discussing SpiceDB schema design)
- The `authorization-planner` skill routes you to a command

### Commands

Claude Code slash commands (`/spicedb-dev:plan`) correspond to command files. In Codex,
reference them by name:

| Claude Code command | Codex equivalent |
|---|---|
| `/spicedb-dev:plan` | "Run the plan command" or read `commands/plan.md` |
| `/spicedb-dev:design-model` | "Run the design-model command" |
| `/spicedb-dev:generate-schema` | "Run the generate-schema command" |
| `/spicedb-dev:validate-schema` | "Run the validate-schema command" |
| `/spicedb-dev:implement-spicedb` | "Run the implement-spicedb command" |
| `/spicedb-dev:implement-spicedb-checks` | "Run the implement-spicedb-checks command" |
| `/spicedb-dev:implement-spicedb-relationships` | "Run the implement-spicedb-relationships command" |
| `/spicedb-dev:audit-coverage` | "Run the audit-coverage command" |
| `/spicedb-dev:test-permissions` | "Run the test-permissions command" |

## Updating

```bash
cd ~/.codex/authzed-marketplace && git pull
```

Skills update instantly through the symlink.

## Uninstalling

```bash
rm ~/.agents/skills/spicedb-dev
```

**Windows (PowerShell):**
```powershell
Remove-Item "$env:USERPROFILE\.agents\skills\spicedb-dev"
```

Optionally delete the clone: `rm -rf ~/.codex/authzed-marketplace`
(Windows: `Remove-Item -Recurse -Force "$env:USERPROFILE\.codex\authzed-marketplace"`).

## Troubleshooting

### Skills not showing up

1. Verify the symlink: `ls -la ~/.agents/skills/spicedb-dev`
2. Check skills exist: `ls ~/.codex/authzed-marketplace/spicedb-dev/skills`
3. Restart Codex -- skills are discovered at startup

### Agent dispatch not working

Verify `multi_agent = true` in `~/.codex/config.toml`. Without it, `spawn_agent`
is not available and agents fall back to inline execution.

### Windows junction issues

Junctions normally work without special permissions. If creation fails, try running
PowerShell as administrator.
