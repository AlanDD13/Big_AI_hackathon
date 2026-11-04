# Join the five-person rover team

Repository: https://github.com/AlanDD13/Big_AI_hackathon (private).
Human lead and shared-file owner: **AlanDD13**.
Design: https://www.figma.com/board/bmW81xDifPQz5Icv98D8Hd/Big-AI?node-id=26-995.
Message Board: https://github.com/AlanDD13/Big_AI_hackathon/issues/1.

## Assigned areas

| GitHub login | Work | Exclusive paths | First slice |
| --- | --- | --- | --- |
| LcyShox | Dashboard | `frontend/**` | Fixture map, object and mission status with API mocks |
| AlanDD13 | Detection/tracking | `vision/**` | Recorded clip to stable pixel tracks and matching frames |
| zekihxh | Backend/Grok | `backend/**` | Mapped observation to mission using fake mapping/robot clients |
| platonyesin-dev | Robot execution | `robot/**` | Command to simulated execution, status and stop |
| Tezqx | Mapping/localization/routes | `mapping/**` | Pixel fixture to metric point, rover pose and route |

Everyone keeps module tests, mocks and dependencies in their own directory and
participates in integration. Alan owns shared `contracts/`, `integration/`,
`infrastructure/`, `docs/`, `.github/`, `tools/`, root tests/config and coordination
files. Request a handoff before touching another owner's paths.

## Alan: before teammates start

1. Invite the other four accounts through [Settings > Collaborators](https://github.com/AlanDD13/Big_AI_hackathon/settings/access).
   They need write access to push task branches. Each must accept the invitation;
   pending invitations are not accepted access.
2. Review and merge the onboarding setup PR, then forward the message below.
3. Agree the draft contracts with all owners and land schemas/fixtures before
   implementation branches diverge. Application code is not included here.
4. Use PRs and keep `main` runnable. Only Alan merges. If your GitHub plan supports
   protection for this private repo, require PR reviews and prevent force pushes.
   CODEOWNERS is review routing, not a file lock.

## Message to forward

Send after this onboarding setup is merged. Do not include credentials.

```text
Team — our private repo is https://github.com/AlanDD13/Big_AI_hackathon
Please accept the GitHub invitation using your own account, then follow:
https://github.com/AlanDD13/Big_AI_hackathon/blob/main/docs/ONBOARDING.md

LcyShox → frontend/ (dashboard)
AlanDD13 → vision/ (detection and tracking; also human lead)
zekihxh → backend/ (backend and Grok)
platonyesin-dev → robot/ (rover execution)
Tezqx → mapping/ (mapping, localization and route planning)

Read AGENTS.md and docs/CONTRACTS.md. Confirm you can clone and read board issue #1.
We will agree API fixtures first. Then use one task branch per task, edit only
your assigned folder, and open PRs for Alan to review/merge. Keep module tests,
mocks and dependencies in your folder. Ask Alan before shared-file changes.
Do not push directly to main. Send setup problems in our team chat.
```

## Everyone: clone on your own laptop

Install Git, Bash and GitHub CLI. On Windows use WSL or Git Bash. Accept the
invitation with your own account before cloning. Then run:

```bash
gh auth login -h github.com
git clone https://github.com/AlanDD13/Big_AI_hackathon.git
cd Big_AI_hackathon
tools/board whoami
tools/board read -n 50
tools/board unread
```

`whoami` must show your actual account. Open this clone in Codex, Cursor,
Claude Code or Gemini CLI. Each teammate needs a separate clone and account.
Use one active board reader/agent per clone. The board cannot wake closed chats.

## Prompt to paste into your coding agent

Replace every bracketed field. Use your module README for the first slice and
its completion check. You choose your own coding agent.

```text
We are building the rover project in AlanDD13/Big_AI_hackathon.
Read AGENTS.md, docs/ONBOARDING.md, docs/CONTRACTS.md and [my-folder]/README.md.
My name is [name], GitHub login [login], agent name [Codex/Cursor/Claude/Gemini].
My assigned paths are [my-folder]/**. Human lead is AlanDD13.
Run tools/board whoami, tools/board read -n 50, then tools/board unread.

My task: [first slice from my module README].
Done means: [module README completion check].
If contracts are still draft, inspect my task and propose its inputs/outputs;
do not invent an incompatible API or start product implementation yet.
After the lead lands agreed contracts, fetch origin and create
task/[login]/[short-task] from origin/main. Post CLAIM with task, branch and paths.
Implement only my task. Keep tests, mocks and dependencies in my folder.
Read the board before commits, after merges and at task boundaries.
Post useful REQUEST/BLOCKED updates; do not send acknowledgement loops.
Open a PR with behavior, tests, dependencies and any agreed contract change;
post READY with its URL. Alan reviews and merges. Do not merge or deploy.
I authorize task-coordination comments to this repository's Message Board issue #1.
```

## Branch and PR workflow

After contracts are agreed, replace LOGIN and TASK and create your task branch:

```bash
git fetch origin
git switch -c task/LOGIN/TASK origin/main
```

Stage explicit files, run your module's checks, commit, push your task branch,
and open a PR targeting `main`. The PR template records behavior, checks and
dependencies. Post READY on the board; Alan reviews and merges.
An unmerged PR is not on `main`: wait or coordinate use of its specific branch.
After merge, start the next task from fresh `origin/main`.

Do not edit another owner's files to resolve dependencies without a handoff.
Do not commit `.env`, tokens, `.board/`, virtual environments or generated builds.

## Review routing

`.github/CODEOWNERS` records module owners and routes unmatched paths to Alan.
It requires invitees to accept write access and the file to be on the base branch.
Availability in private repos depends on the GitHub plan. It does not prevent
cross-folder edits; mandatory review enforcement needs supported repository rules.

References: [GitHub CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners),
[detailed board setup](TEAM_SETUP.md).
