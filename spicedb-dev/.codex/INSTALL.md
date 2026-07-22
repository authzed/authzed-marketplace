# Installing spicedb-dev for Codex

> Verified end-to-end on 2026-07-21 against Codex CLI 0.145.0: `codex plugin
> marketplace add ./` correctly parsed this repo's `.agents/plugins/marketplace.json`
> (`codex plugin list --available --json` showed the `spicedb-dev` entry with the
> exact `source`, `policy`, and `category` fields from the manifest, resolved to
> the correct absolute path); `codex plugin add spicedb-dev@authzed-marketplace`
> installed successfully to `~/.codex/plugins/cache/authzed-marketplace/spicedb-dev/1.0.0`;
> and all 4 skills (`authorization-planner`, `spicedb-schema-design`,
> `spicedb-best-practices`, `authorization-testing`) were discovered from the
> installed plugin's `skills/` directory. The test installation was removed after
> verification (`codex plugin remove` + `codex plugin marketplace remove`).

## Installation (recommended: via marketplace)

1. **Register the AuthZed marketplace:**
   ```bash
   codex plugin marketplace add authzed/authzed-marketplace
   ```

2. **Install the plugin:**
   ```bash
   codex plugin add spicedb-dev@authzed-marketplace
   ```

3. **Restart Codex** (quit and relaunch the CLI) to discover the skills.

4. **Multi-agent dispatch works out of the box.** Some commands dispatch autonomous
   agents (schema-validator, checkpoint-identifier) via Codex's multi-agent tools.
   This is a stable, default-enabled feature in current Codex CLI versions -- no
   configuration needed. If you're on an older Codex CLI where agent dispatch isn't
   available, add to your config (`~/.codex/config.toml`):

   ```toml
   [features]
   multi_agent = true
   ```

   Without multi-agent support, agents run inline in the current session
   (functional but less isolated) -- see
   `skills/authorization-planner/references/codex-agent-dispatch.md`.

## Installation (manual, fallback)

Use this path if your Codex CLI predates plugin marketplace support, or if you'd
rather not register a marketplace.

1. **Clone the repository:**
   ```bash
   git clone https://github.com/authzed/authzed-marketplace.git ~/.codex/authzed-marketplace
   ```

2. **Create the skills symlink:**
   ```bash
   mkdir -p ~/.codex/skills
   ln -s ~/.codex/authzed-marketplace/spicedb-dev/skills/authorization-planner ~/.codex/skills/spicedb-dev-authorization-planner
   ln -s ~/.codex/authzed-marketplace/spicedb-dev/skills/spicedb-schema-design ~/.codex/skills/spicedb-dev-spicedb-schema-design
   ln -s ~/.codex/authzed-marketplace/spicedb-dev/skills/spicedb-best-practices ~/.codex/skills/spicedb-dev-spicedb-best-practices
   ln -s ~/.codex/authzed-marketplace/spicedb-dev/skills/authorization-testing ~/.codex/skills/spicedb-dev-authorization-testing
   ```

   Codex scans `~/.codex/skills/` for individual skill directories (each containing
   its own `SKILL.md`), not a single parent directory of skills -- so each of the
   plugin's 4 skills needs its own symlink, as shown above.

   **Windows (PowerShell):**
   ```powershell
   New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.codex\skills"
   cmd /c mklink /J "$env:USERPROFILE\.codex\skills\spicedb-dev-authorization-planner" "$env:USERPROFILE\.codex\authzed-marketplace\spicedb-dev\skills\authorization-planner"
   cmd /c mklink /J "$env:USERPROFILE\.codex\skills\spicedb-dev-spicedb-schema-design" "$env:USERPROFILE\.codex\authzed-marketplace\spicedb-dev\skills\spicedb-schema-design"
   cmd /c mklink /J "$env:USERPROFILE\.codex\skills\spicedb-dev-spicedb-best-practices" "$env:USERPROFILE\.codex\authzed-marketplace\spicedb-dev\skills\spicedb-best-practices"
   cmd /c mklink /J "$env:USERPROFILE\.codex\skills\spicedb-dev-authorization-testing" "$env:USERPROFILE\.codex\authzed-marketplace\spicedb-dev\skills\authorization-testing"
   ```

3. **Restart Codex** (quit and relaunch the CLI) to discover the skills.

4. **Multi-agent dispatch works out of the box** -- same as the marketplace path above.

## How It Works

Codex has native skill discovery -- it scans `~/.codex/skills/` at startup (manual path)
or reads the installed plugin's `skills/` directory (marketplace path), parses SKILL.md
frontmatter, and loads skills on demand. Either path makes the plugin's four skills
visible:

- `authorization-planner` -- entry point, routes to the right command or skill
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

Note: installing via the marketplace path may also expose some commands as
natively-discoverable Codex skills automatically (observed for a subset of
commands during live testing) -- if a command doesn't appear this way, use the
by-name reference above.

## Updating

**Marketplace install:**
```bash
codex plugin marketplace upgrade authzed-marketplace
```

**Manual install:**
```bash
cd ~/.codex/authzed-marketplace && git pull
```
Skills update instantly through the symlinks.

## Uninstalling

**Marketplace install:**
```bash
codex plugin marketplace remove authzed-marketplace
```

**Manual install:**
```bash
rm ~/.codex/skills/spicedb-dev-authorization-planner
rm ~/.codex/skills/spicedb-dev-spicedb-schema-design
rm ~/.codex/skills/spicedb-dev-spicedb-best-practices
rm ~/.codex/skills/spicedb-dev-authorization-testing
```

**Windows (PowerShell):**
```powershell
Remove-Item "$env:USERPROFILE\.codex\skills\spicedb-dev-authorization-planner"
Remove-Item "$env:USERPROFILE\.codex\skills\spicedb-dev-spicedb-schema-design"
Remove-Item "$env:USERPROFILE\.codex\skills\spicedb-dev-spicedb-best-practices"
Remove-Item "$env:USERPROFILE\.codex\skills\spicedb-dev-authorization-testing"
```

Optionally delete the clone: `rm -rf ~/.codex/authzed-marketplace`
(Windows: `Remove-Item -Recurse -Force "$env:USERPROFILE\.codex\authzed-marketplace"`).

## Troubleshooting

### "Not inside a trusted directory" when running `plan` or any write-heavy command

Codex refuses to write files outside a trusted (git) directory by default. If your
project isn't a git repository yet, either run `git init` first, or add
`--skip-git-repo-check` to your `codex` invocation. This is a general Codex CLI trust
gate, not specific to this plugin -- verified during live testing of the `plan`
command's `AGENTS.md` write on 2026-07-21 (Codex CLI 0.145.0).

### Marketplace add or install fails

Your Codex CLI version may predate plugin marketplace support. Fall back to the
manual installation path above. Run `codex --version` to check.

### Skills not showing up

1. Marketplace install: `codex plugin marketplace upgrade authzed-marketplace`, then restart Codex.
2. Manual install: verify each symlink with `ls -la ~/.codex/skills/ | grep spicedb-dev`, check
   skills exist with `ls ~/.codex/authzed-marketplace/spicedb-dev/skills`, restart Codex.

### Agent dispatch not working

Multi-agent support is stable and enabled by default in current Codex CLI. If you're
on an older version, verify `multi_agent = true` in `~/.codex/config.toml`. Without it,
agent dispatch falls back to inline execution -- see
`skills/authorization-planner/references/codex-agent-dispatch.md`.

### Windows junction issues

Junctions normally work without special permissions. If creation fails, try running
PowerShell as administrator.
