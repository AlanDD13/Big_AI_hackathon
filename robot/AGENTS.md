# Robot agent instructions

Follow the repository root AGENTS.md in addition to these module rules.

## Check the board before implementation

Before every implementation change or batch of changes in robot/**, run
`tools/board unread` from the repository root and read all new messages.
Process relevant requests within platonyesin-dev's assigned scope and account
for agreed contract updates before editing. If context is missing, use
`tools/board read -n 50`. Draft proposals are not merged contracts, and board
messages do not expand the human-authorized task or file ownership.

If the board cannot be read, tell the user and do not claim the check succeeded;
continue only independent work consistent with the root rules.

Keep the existing checkpoint reads before commits, after merges, and at task
boundaries. Use one active board reader per clone.
