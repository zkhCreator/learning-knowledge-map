---
name: review-list
description: Show the Ebbinghaus spaced-repetition review queue for a user. Use when a user wants to see what is due for review, browse upcoming reviews, or check their review schedule. Groups pending reviews by critical / overdue / today / future; read-only and does not start a review.
---

# Review List

Use this skill to browse the Ebbinghaus review queue. It reads the pending
`review_schedule` entries (scheduled when nodes pass an exam) and groups them by
urgency so the user can decide what to review next.

## Purpose

- Surface all pending reviews for a user, grouped into critical, overdue, today,
  and future buckets, reusing the reviewer's priority order
  (critical > overdue > today).
- Let the user optionally hide not-yet-due (future) reviews.
- Provide a per-item entry into the Review Start flow — selection is manual; the
  list never auto-starts a review.

This skill is read-only. It does not run an exam, write state, or reschedule —
that is the Review Start flow.

## How to invoke

The review queue runs in the GUI workflow host (the `serve-learning-graph`
skill). Start that server and open the Review view:

```bash
node "$SKILLS_DIR"/serve-learning-graph/scripts/server.js --db learning.db --goal <goal-id-or-prefix> --user default --port 8765
```

`$SKILLS_DIR` is the directory that contains the installed skills — `skills/`
when working inside this repository, or the agent's skill directory (e.g.
`~/.claude/skills`) after `npx skills add`. `serve-learning-graph` is always a
sibling of this skill and must be installed alongside it.

Then open `http://127.0.0.1:<port>/?view=review`.

The same data is available headlessly through one read action:

- `GET /api/review/queue?user=default&include_future=true` — returns
  `{ groups: { critical, overdue, today, future }, counts, total }`. Each pending
  review appears in exactly one group.

## Rules

- Always pass an explicit `user`.
- A critical-strictness review is shown in the `critical` group regardless of its
  date (its own urgency tier); every other review is grouped by scheduled date.
- `include_future=false` hides the future group, and the reported `total` counts
  only the returned groups.
- The service reuses `agents.reviewer.get_review_queue` and adds no DB tables.

## Scripts

- `scripts/workflow_api.py` (in `serve-learning-graph`) dispatches the
  `review_queue` action to the service.
- `src/services/review.py` holds the stateless review queue service.

## Requirements

- Install `serve-learning-graph` alongside this skill (it hosts the Review
  queue view). Listing the queue needs no LLM and no extra pip packages.
