---
description: Review this phase's changes against the service's invariants with the read-only rules-reviewer agent
argument-hint: [base ref; default main]
---

Review the changes since `$ARGUMENTS` (if that is empty, use `main`; before the first commit, review every file
that `git status` shows).

1. Collect the changes: `git diff <base>...HEAD`, the uncommitted diff, and the new files from `git status`.
2. Give the `rules-reviewer` agent the list of changed files and the diff, and ask it to check them against its
   invariants. It may only read.
3. Show its findings as a table: number, file and line, the invariant, and your verdict. Every finding is either
   **fixed** (fix it now) or **rejected** with a one-line reason. Never drop a finding silently.
4. Keep the table with the phase's notes.
