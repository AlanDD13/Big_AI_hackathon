# Cursor project setup

Open this repository folder in Cursor and start a new Agent chat. Project
rules and 13 skills are stored here, so teammates receive them with this branch.
Read docs/CURSOR_HANDOFF.md for Alan's continuation prompt and current blockers.
Skill installation does not transfer Codex chats, OAuth sessions or running agents.

The two project skills are hackathon-vision and hackathon-board. Nine portable
engineering skills cover planning, incremental implementation, source checking,
API design, tests, debugging, security, code review and Git. Supabase development
and Postgres best practices complete the set. Load only the relevant skills.
They should appear in Cursor's skill list after reopening/reloading the project;
invoke /hackathon-vision explicitly to begin. Runtime discovery still needs a
fresh Cursor chat; no model call or subscription change was made during setup.

The background board-coordinator definition preserves Alan's requested workflow.
Its model inherits the model selected in Cursor; select a model available in
your Cursor account. The previous Codex agent instances have stopped and do
not migrate into Cursor. One reader per checkout remains mandatory.

## Tools and authentication

- GitHub coordination uses the existing tools/board and gh CLI, not a copied
  token. Run tools/board whoami to verify your own account.
- mcp.json configures official Supabase MCP, scoped to project
  buojgmoumyzqxmerckjm with read-only database/docs/debugging tools. In Cursor
  Settings > Tools & MCP, connect/authenticate Supabase using your own account.
  This config grants no access by itself. Backend writes/migrations and runtime
  roles still belong to zekihxh; do not remove read-only mode as a workaround.
- The Grok application uses XAI_API_KEY in the terminal/runtime environment.
  No API keys or project credentials are bundled here.
- Codex-only Figma connectors were not copied as skills: tool connections must
  be connected separately if a later task actually needs Figma editing.

## Skill provenance

Engineering workflows and supporting references are copied unchanged from the
locally installed Agent Skills plugin 0.6.10:
https://github.com/addyosmani/agent-skills (MIT; license in skills/workflows/).
The references directory preserves those workflows' relative links.

Supabase skills/references are copied unchanged from installed Supabase plugin
1.0.0: https://github.com/supabase-community/supabase-plugin (MIT; license in
each skill directory). No plugin executable or credentials were copied.

Project rules, project skills and board-coordinator instructions were authored
for this repository. Human assignments and repository rules take precedence
over generic examples in imported skills.

Official format references:
https://cursor.com/docs/skills,
https://cursor.com/docs/rules,
https://cursor.com/docs/subagents,
https://cursor.com/docs/mcp,
https://supabase.com/docs/guides/ai-tools/mcp.
