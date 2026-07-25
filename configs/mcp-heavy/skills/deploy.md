---
name: deploy
description: Ship the current branch to staging or production through the audited deploy pipeline.
---

# Deploy

Run the deploy pipeline for the current branch. Staging deploys are
self-service; production deploys require an approved change ticket number
passed as the first argument.

Steps: verify the working tree is clean, confirm CI is green for the head
commit, then trigger the pipeline with `deployctl release --env <env>`.
Watch the rollout until all pods report ready. If any pod crash-loops,
roll back immediately with `deployctl rollback --env <env>` and report the
failing pod logs.

Never deploy to production on a Friday after 15:00 local time unless the
change is a rollback or an approved incident fix.
