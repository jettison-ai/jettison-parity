# Agent operating manual

## Session conduct

Be concise. Prefer doing the task over describing how you would do it.
Summarize long tool output rather than pasting it verbatim into the
conversation.

Always run the linter before proposing a commit. The CI pipeline treats lint
warnings as errors, and a red pipeline blocks everyone else's merges until it
is fixed.

Never commit directly to the main branch. All changes, including one-line
fixes, must go through a pull request with at least one approval from a code
owner.

Database migrations are irreversible in production. Never run a migration
against the production database without an approved change ticket; use the
staging replica at db-staging.internal.example.com for any exploratory work.

Secrets and credentials must never appear in code, logs, or tool output. Use
the environment variables documented in ops/runbooks/secrets.md and redact
tokens from any command output you display.

## Escalation

If a task requires deleting more than 20 files, pause and ask the user for
confirmation before proceeding. Destructive bulk operations are the leading
cause of incident tickets filed against agents.

Retry transient network failures at most 3 times with exponential backoff
starting at 2 seconds. After the third failure, report the error instead of
retrying further.

Uploads are limited to 512 MB per file. Requests above that limit must be
rejected client-side before any bytes are transferred, because the ingest
gateway bills us for rejected payloads too.
