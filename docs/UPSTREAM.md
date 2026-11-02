# Upstream provenance

- Repository: https://github.com/azizu06/hackathon-agent-board
- Inspected revision: `91a9f0120c05c0efaf7b0e52bc96c28de8b4486c`
- Inspected on: 2026-11-02
- `tools/board` is adapted from that revision, not installed from a moving branch.

Local changes:

- Read shared `board.conf` as data, with environment overrides; require an
  explicit positive issue number instead of assuming #1.
- Keep per-checkout state beside the script, including when invoked in a subfolder.
- Help works without a configured board; whoami fetches the live account.
- Reject missing read/wait arguments and a zero polling interval; cap the sleep
  to the remaining timeout instead of overshooting it by an entire interval.
- Document actual behavior: wait wakes for any other account's new comment;
  mentions are highlighted; updates to old comments may not be delivered.
- Team guide, shared rules, Gemini import, and contract template are local.
  No upstream Claude hooks or banner image are installed.

No LICENSE file was present in the inspected upstream tree. Attribution here
does not establish an open-source license; do not label the upstream script
as MIT/Apache or relicense it without checking with its author.

Verification: offline tests use a fake gh executable, exercise comments,
cursors, mentions, errors, argument handling, configuration, and bounded waits.
A live two-account round trip remains to be done once a team repository exists.
