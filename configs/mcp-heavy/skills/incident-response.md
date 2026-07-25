---
name: incident-response
description: Triage a production incident, coordinate mitigation, and draft the postmortem timeline.
---

# Incident response

When an incident is declared, first acknowledge the page, then establish
severity using the impact matrix in ops/runbooks/severity.md. Post a status
update to the incidents channel every 30 minutes until resolution.

Mitigate before you diagnose: rolling back the most recent deploy resolves
the majority of production incidents. Capture every action taken with its
timestamp; the postmortem timeline is assembled from these entries.

Never modify production data during an incident without a second responder
explicitly approving the exact command in the incidents channel.
