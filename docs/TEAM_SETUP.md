# Team setup and daily use

For the current five-person assignments, copy-paste teammate message and first
tasks, start with [ONBOARDING.md](ONBOARDING.md). That document and `AGENTS.md`
contain current ownership; the examples below explain the tooling.

## 1. Decide whether to use it

Use this if several teammates will build interdependent parts using coding
agents with shell access. The board shares progress, requests, and contract
changes across laptops and agent vendors. Agents keep their own chats and code;
GitHub stores only the comments you post and the code you push.

It does not resolve merge conflicts, lock files, share model context, supervise
agents, or wake a stopped chat. Keep one human integrator and one clear owner
per area. If everyone is changing the same small module, work sequentially.
No paid board service or model API key is needed. Normal agent subscriptions
and GitHub access still apply. Check the event's rules for AI tools and any
pre-event code restrictions; this starter contains coordination tooling only.

The linked Notion page is the author's board walkthrough, not this event's rules.

## 2. Repository and board: already configured

- Repository: https://github.com/AlanDD13/Big_AI_hackathon (private).
- Pinned Message Board: https://github.com/AlanDD13/Big_AI_hackathon/issues/1.
- Origin: https://github.com/AlanDD13/Big_AI_hackathon.git.
- Shared configuration: `board.conf`; every clone gets the same board.
- Owner/account: `AlanDD13`. Created and connected on 2026-11-03.

Do not create another repository or another board. Alan's existing local clone
is `/Users/alandd/Desktop/Pavia_grok_hackathon`; that folder name can stay as is.

In [Settings -> Collaborators](https://github.com/AlanDD13/Big_AI_hackathon/settings/access),
invite each human teammate with write access; they accept the invitations.
Each teammate uses their OWN GitHub account. Keep Issues enabled. The repo is
private; invited teammates must accept access before cloning.

Inside an agent sandbox, network/keychain restrictions may produce a misleading
login error. Retry a read through the approved permission flow or in your normal
terminal before replacing a working credential. Never paste a token into an
agent chat or a tracked file.

## 3. Lead: agree ownership and contracts at kickoff

The five GitHub logins and exclusive paths are recorded in `AGENTS.md`.
`AlanDD13` is the human lead and shared-file owner. Module owners keep their
dependencies, tests and mocks inside their own directory.

Agree `docs/CONTRACTS.md` before splitting work: one demonstrable user flow,
request/response JSON examples, error behavior, data format, and local run
commands. Give each task a completion check and integrate frequently.

Commit and push the agreed ownership/contracts before teammates start their
implementation branches. FigJam is the design proposal; `docs/CONTRACTS.md`
must explicitly record which interfaces have been agreed.

`BOARD_REPO` and `BOARD_ISSUE` environment variables override `board.conf`.
If the wrong board appears, remove old overrides with
`unset BOARD_REPO BOARD_ISSUE` and retry.

## 4. Everyone: install and join on their own laptop

Install Git, Bash, and GitHub CLI. On a Mac with Homebrew: `brew install gh`.
On Windows, use WSL or Git Bash plus GitHub CLI; PowerShell alone cannot execute
the Bash script. Official installers: https://cli.github.com/.

After accepting the repository invitation, run from your own terminal:

```bash
gh auth login -h github.com
git clone https://github.com/AlanDD13/Big_AI_hackathon.git
cd Big_AI_hackathon
tools/board whoami
tools/board read -n 50
tools/board unread
```

`whoami` must show YOUR login. Open THIS clone in your coding agent. The agent
needs access to Bash/`gh`, the project, and GitHub's network. Your regular terminal
being authenticated does not guarantee a remote/cloud agent is authenticated.

Use one active coding agent/board reader per clone. Two readers in the same
clone share `.board` cursors and can consume each other's unread messages. Two
agents using one GitHub account will also hide each other's comments as self
posts, even across separate clones. Start with one account per person and one
active agent per person.

The `.board/` directory is local ignored state. Never commit it. `read` does not
advance its cursor; `unread` and `wait` do. First `unread` shows only the most
recent 10 comments, so explicitly read more context when joining late.

## 5. Start Codex, Cursor, Gemini CLI, or Claude Code

- **Codex:** open your clone as the project. The shared instructions are in
  `AGENTS.md`. Paste the starter prompt below with your task and ownership.
- **Cursor:** open the clone and use Agent mode with terminal access. Cursor
  supports root `AGENTS.md`; paste the same prompt and verify it reads the file.
- **Gemini CLI:** start it in the clone. `GEMINI.md` imports `AGENTS.md`. Use
  `/memory show` to check the rules loaded; reload/restart after changing them.
  Gemini in a plain browser chat cannot execute the script.
- **Claude Code:** start it in the clone; `CLAUDE.md` imports `AGENTS.md`. This
  adaptation uses explicit reads for all agents and does not install the
  upstream Claude background hooks.
- **Other agents:** explicitly attach/read `AGENTS.md` and give terminal access.

Replace every placeholder in this prompt before sending it:

```text
We are using this repository's GitHub agent board.
Read AGENTS.md and docs/CONTRACTS.md, then run tools/board whoami and
tools/board unread. My GitHub login is <login>; my name is <name>.
You are <agent>. Prefix board posts with [<name>'s <agent>].
The human team lead is <lead-login>. I own <paths>.
My task is <specific task>; done means <observable check>.
Create a task branch from current origin/main. Post CLAIM with the branch,
task, and paths. Implement only my task; coordinate dependencies on the
board. Check unread before commits and after merges. Open a PR with checks
and post READY; the lead merges it. Do not merge or deploy without my instruction.
```

The team's normal permissions still apply. If you want agents to post without
requesting approval each time, explicitly authorize comments to your team's
Message Board issue for task coordination. Do not broadly disable approvals.

## 6. Two-person test before the hackathon

Replace `teammate-login` with a real login. Teammate B opens their agent and
has it execute the wait command below. Teammate A then posts:

```bash
tools/board post "[Alan's Codex] REQUEST: @teammate-login please reply with your agent name and assigned area; board connection test."
```

On B's machine, in the active agent:

```bash
tools/board wait --interval 15 --timeout 45
```

Expected: A's comment appears with `MENTION`. B's agent replies on the board
using its actual identity and tags `@AlanDD13`. A runs `tools/board unread` and
confirms the reply. `tools/board read -n 20` on BOTH machines must show both
comments. B should check within the 45-second window, or run the wait again.

If no one posts, the bounded wait exits 124. That is a timeout, not a failed
login. The script wakes for ANY new comment from another account, including
ones not addressed to you. Only relevant mentions require action.

## 7. How to use it during the build

Example handoff (replace the names/logins/paths with your team's values):

```text
[Alan's Codex] CLAIM: results UI, task/AlanDD13/results, frontend/ only.
[Mira's Gemini] READY: POST /analyze on PR #12; response matches docs/CONTRACTS.md; API checks passed. @AlanDD13 UI can use the agreed mock until merge.
[Lead's Cursor] MERGED: PR #12, commit abc1234; @AlanDD13 fetch main for the endpoint.
[Alan's Codex] BLOCKED: @mira-login API returns score as text; agreed type is number. Please fix backend/; UI remains on the mock.
```

Board messages do not transfer code: use Git to fetch branches and integrate
PRs. Only call a feature merged when it is actually on `main`. After your own
PR is merged, start the next task from updated `origin/main`.

Use a single integrator to keep a runnable `main`, check one complete demo
flow after integration, and preserve a working demo when trying improvements.
Use normal verbal/team chat for urgent ambiguity rather than waiting on a bot.
Post at task/dependency boundaries; avoid a stream of tiny updates.

## 8. Receiving messages while agents work

Start with **checkpoint reads**, which work across vendors: task start, before
commit, after a merge, and while checking a blocker. They use no always-on
process and cannot wake a stopped agent.

If you want an active agent to wait between tasks, add this instruction:

```text
Stay available in this active session. Keep one tools/board wait --interval 15
--timeout 45 command pending when idle. Process its output, act only within my
assigned scope, and re-arm it after each wake/timeout until I stop you.
If you cannot keep a tool running or continue the session, tell me explicitly.
```

A GitHub mention does not wake a closed Codex/Cursor/Gemini session. Whether a
pending tool result resumes the agent depends on its runner, limits, and
permissions. Verify this with the two-person test in your actual clients.

For a HUMAN-visible watcher, use a spare terminal in your clone:

```bash
while true; do
  tools/board wait --interval 30 --timeout 60
  result=$?
  if [ "$result" -ne 0 ] && [ "$result" -ne 124 ]; then break; fi
done
```

Stop with Ctrl-C. This prints messages and advances the same local cursor;
it does NOT send them to your agent. If you use this watcher, be its sole
board reader and relay messages to the agent, or have the agent recover with
`tools/board read -n 20`. Do not assume its next `unread` will show consumed posts.

Do not use the walkthrough's generic `while wait; do <agent>; done` without an
adapter: wait has already marked the messages read. A headless integration
must pass the actual wait OUTPUT into a persistent/resumed agent session,
serialize invocations, and handle permissions/errors. That vendor-specific
automation is not installed here. It is optional; checkpoint reads are enough
to start the hackathon.

## Troubleshooting

- **BOARD_ISSUE unset:** pull the configured main branch; board.conf should target issue 1. Check environment overrides.
- **Cannot determine repo:** set BOARD_REPO in board.conf and verify origin.
- **404:** check repository, issue number, invitation acceptance, and login;
  private resources can return 404 when your account lacks access.
- **No new posts:** check same-account filtering and other readers. Use read
  to inspect history. Old comment edits are not reliable notifications; post new.
- **Wrong mentions/login after switching accounts:** `whoami` uses a live API
  request, while unread caches login. Delete only `.board/login`, then retry.
- **Wait silently times out:** it also retries transient GitHub failures. Run
  `tools/board unread` and `gh auth status` to see errors immediately.
- **Permission denied running script:** `chmod +x tools/board` or use
  `bash tools/board ...`. On Windows prefer a clone made inside WSL.
- **Rate limits/noisy board:** default polling is 30 seconds, 15 in the short
  test. No-message polls inside one wait are about one request each; startup
  and comment retrieval use more. Do not run many redundant readers.

## Sources

- Original script and workflow: https://github.com/azizu06/hackathon-agent-board
- Author's walkthrough: https://marvelous-relative-5de.notion.site/hack
- GitHub CLI: https://cli.github.com/manual/gh_repo_create,
  https://cli.github.com/manual/gh_issue_create,
  https://cli.github.com/manual/gh_issue_pin
- Cursor rules and AGENTS.md: https://cursor.com/docs/rules
- Gemini context/imports: https://geminicli.com/docs/cli/gemini-md/
