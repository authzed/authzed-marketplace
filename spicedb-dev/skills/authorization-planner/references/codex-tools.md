# Codex Tool Mapping

Commands and skills use Claude Code tool names. When running in Codex, use your
platform equivalent:

| Command/skill references | Codex equivalent |
|---|---|
| `AskUserQuestion` | Ask the user directly in your response (no dedicated tool) |
| `TaskCreate` / `TaskUpdate` | `update_plan` |
| `Task` tool (launch agent) | See `references/codex-agent-dispatch.md` |
| `Skill` tool (invoke a skill) | Skills load natively -- follow the instructions |
| `Read`, `Write`, `Edit`, `Glob`, `Grep`, `Bash` | Use your native file and shell tools |

## Notes

- The `allowed-tools` field in command frontmatter is Claude Code-specific. Codex
  should ignore it -- it does not restrict tool access in Codex.
- Claude Code slash commands (`/spicedb-dev:plan`) correspond to command files in
  `commands/`. When a skill or user references a slash command, read and follow
  the corresponding `commands/<name>.md` file.
- For agent dispatch details (schema-validator, checkpoint-identifier), see
  `references/codex-agent-dispatch.md`.
