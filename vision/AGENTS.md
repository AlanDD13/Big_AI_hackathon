# Vision task coordination

Follow the repository-root AGENTS.md and docs/CONTRACTS.md.

The active coordinating agent MUST run `tools/board unread` at task start,
before committing, after merges, and at implementation/integration boundaries.
Also check the board BEFORE spawning any new subagent, so its task reflects
current requests and agreed interfaces. Relay relevant messages in its brief.
On first joining also run `tools/board read -n 50`. Read and act on relevant
requests before assuming an interface is agreed. Record unresolved dependencies
and post useful REQUEST, BLOCKED or READY updates with exact GitHub mentions.

Use ONE board reader per checkout. Spawned subagents must not read/post the
board or switch branches: send findings to the coordinating agent, which reads
and relays relevant board messages. Each teammate uses their own clone/account.

Do not claim that a posted request reached another agent without evidence.
Board comments do not wake closed chats. Use the bounded wait workflow from the
root guide only when the human explicitly asks the active session to wait.

Alan explicitly requested a dedicated coordination agent for this implementation.
Run that agent in its OWN clone and board cursor, not this shared checkout.
It may read the board, resolve routine messages using the agreed contracts and
post actionable replies. It must not reassign ownership, edit another owner's
paths, merge/deploy, or invent contract decisions. Escalate only blockers,
conflicts, requested approvals or decisions not covered by the current plan.
Before commits/task boundaries, obtain its latest summary and read this
checkout's unread messages as required by the root rules.

Own only vision/** and the lead-authorized shared contract/docs changes.
Backend Supabase schema/storage, dashboard UI, mapping transforms and DJI connection stay with
their assigned owners unless they explicitly grant a handoff.

Always preserve the five-second stationary gate, living/carried exclusions,
mapping quality checks and stale-result revocation. A detector score is not a
calibrated probability. Fixture classification must be labelled and never
mistaken for live Grok evidence. Do not claim unperformed hardware/API tests.
