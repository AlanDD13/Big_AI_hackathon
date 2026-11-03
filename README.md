# Big_AI_hackathon

Local starter for a team using different coding agents. The project itself is
not chosen or implemented yet. Coordination runs through a shared GitHub issue
using `tools/board`, adapted from
[hackathon-agent-board](https://github.com/azizu06/hackathon-agent-board).

**The private repository and [pinned Message Board](https://github.com/AlanDD13/Big_AI_hackathon/issues/1) are connected.**
Start with [the teammate onboarding guide](docs/TEAM_SETUP.md). The remaining
steps are to invite teammates, agree ownership/contracts, and run the two-person
test in your actual coding agents.

- `AGENTS.md`: shared instructions for Codex, Cursor, and other agents.
- `GEMINI.md` / `CLAUDE.md`: entry files for Gemini CLI and Claude Code.
- `board.conf`: configured for `AlanDD13/Big_AI_hackathon`, issue #1.
- `docs/CONTRACTS.md`: decisions to make before splitting implementation.
- `docs/UPSTREAM.md`: pinned upstream revision and local changes.

Runtime requirements: Bash, Git, and authenticated GitHub CLI (`gh`). No model
API key, MCP server, Node service, or board hosting is required. Your coding
agents still use their normal accounts/subscriptions.

Local offline checks: `python3 -m unittest discover -s tests -v`.
