---
name: code-review
description: Review a pull request diff for correctness, style adherence, and missing tests.
---

# Code review

Fetch the pull request diff and review it hunk by hunk. Check for logic
errors first, then style violations against the project code style, then
missing test coverage for changed behavior.

Post findings as inline review comments grouped by file. Use a blocking
review only for correctness issues; style nits are non-blocking. Approve
when there are no correctness findings and tests cover the change.
