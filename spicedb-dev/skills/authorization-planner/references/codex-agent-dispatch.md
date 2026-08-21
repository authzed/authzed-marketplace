# Codex Agent Dispatch

This plugin has two autonomous agents dispatched via the `Task` tool in Claude Code.
In Codex, use `spawn_agent` with the templates below.

Multi-agent support is a stable, default-enabled Codex feature (verified on Codex
CLI 0.145.0) -- no configuration needed. On older Codex CLI versions, enable it
via `[features] multi_agent = true` in `~/.codex/config.toml`.

## Schema Validator

Commands that dispatch this agent: `generate-schema`, `validate-schema`.

When a command says `Task(subagent_type="schema-validator", ...)`:

1. Read `agents/schema-validator.md` and extract the content below the YAML frontmatter
   (everything after the closing `---`)
2. Spawn the agent:

```
spawn_agent(
  agent_type="worker",
  message="Your task is to perform the following. Follow the instructions below exactly.

<agent-instructions>
[content of agents/schema-validator.md, below frontmatter]
</agent-instructions>

Target schema file: {schema_path}

Execute this now. Output ONLY the structured validation report following the format
specified in the instructions above."
)
```

3. Use `wait_agent` to collect the result. There is no explicit close/free-slot step --
   Codex manages the available concurrency slots (4 by default) automatically. Use
   `interrupt_agent` only if you need to stop an agent before it finishes.

Replace `{schema_path}` with the actual path to the `.zed` file being validated.

## Checkpoint Identifier

Commands that dispatch this agent: `implement-spicedb-checks`,
`implement-spicedb-relationships`, `audit-coverage` (Step 8 only).

When a command says `Task(subagent_type="checkpoint-identifier", ...)`:

1. Read `agents/checkpoint-identifier.md` and extract the content below the YAML
   frontmatter (everything after the closing `---`)
2. Spawn the agent:

```
spawn_agent(
  agent_type="worker",
  message="Your task is to perform the following. Follow the instructions below exactly.

<agent-instructions>
[content of agents/checkpoint-identifier.md, below frontmatter]
</agent-instructions>

Resource type: {resource_type}
Language: {language}
Scope: {scope}

Execute this now. Output ONLY the structured checkpoint analysis report following
the format specified in the instructions above."
)
```

3. Use `wait_agent` to collect the result. There is no explicit close/free-slot step --
   Codex manages the available concurrency slots (4 by default) automatically. Use
   `interrupt_agent` only if you need to stop an agent before it finishes.

Replace placeholders:
- `{resource_type}` -- the resource being analyzed (e.g., "document", "project")
- `{language}` -- the application language (e.g., "Go", "TypeScript", "Python")
- `{scope}` -- specific files/directories or "entire codebase"

## Message Framing

The `message` parameter is user-level input, not a system prompt. The templates above
use task-delegation framing ("Your task is...") and XML tags to maximize instruction
adherence. Do not switch to persona framing ("You are...") -- it reduces compliance
in Codex's worker agents.

## Fallback: No Multi-Agent Support

If `spawn_agent` is not available (running on an older Codex CLI without
multi-agent support), execute the agent instructions inline in the current session:

1. Read the agent file (`agents/schema-validator.md` or `agents/checkpoint-identifier.md`)
2. Follow the instructions directly as if they were part of the current command
3. Continue with the parent command after completing the agent's steps

This is less isolated but functionally equivalent. The agent's tools (`Read`, `Bash`,
`Glob`, `Grep`) are available in the current session.
