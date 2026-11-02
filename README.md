# Pavia hackathon workspace

Local starter for a team using different coding agents. The project itself is
not chosen or implemented yet. Coordination runs through a shared GitHub issue
using `tools/board`, adapted from
[hackathon-agent-board](https://github.com/azizu06/hackathon-agent-board).

**Start with [the step-by-step team setup](docs/TEAM_SETUP.md).** It covers
creating the repo, connecting the board, onboarding each agent, and a two-person
test. GitHub CLI authentication and a team repo are still needed.

- `AGENTS.md`: shared instructions for Codex, Cursor, and other agents.
- `GEMINI.md` / `CLAUDE.md`: entry files for Gemini CLI and Claude Code.
- `board.conf`: shared repository/issue coordinates; currently unset.
- `docs/CONTRACTS.md`: decisions to make before splitting implementation.
- `docs/UPSTREAM.md`: pinned upstream revision and local changes.

Runtime requirements: Bash, Git, and authenticated GitHub CLI (`gh`). No model
API key, MCP server, Node service, or board hosting is required. Your coding
agents still use their normal accounts/subscriptions.

Local offline checks: `python3 -m unittest discover -s tests -v`.
