---
name: review-start
description: Run one Ebbinghaus spaced-repetition review for a knowledge node. Use when a user wants to do, execute, or start a due review. Primes the learner with past errors and mnemonic cues, runs a re-exam (reusing the exam flow), then updates state and schedules the next review.
---

# Review Start

Use this skill to execute one spaced-repetition review. It assembles the review
context (historical errors + mnemonic retrieval cues), then runs a re-exam that
reuses the Exam flow, and finally completes the current review and schedules the
next one.

## Purpose

- Prime the learner before the re-exam by showing the node's error-notebook
  history and any mnemonic anchors.
- Run a full re-exam by reusing the Exam primitives (question generation,
  scoring, and finalization) — the re-exam writes mastery state and the error
  notebook just like a first exam.
- Complete the current `review_schedule` entry and schedule the next review,
  rescheduling with a shortened interval when the re-exam is failed so the node
  stays in rotation.

Review is an **independent** flow: its entry, context, and completion logic are
separate from a normal exam, even though it reuses the Exam API for the question
loop. It is never auto-started by another flow.

## How to invoke

Review runs in the GUI workflow host (the `serve-learning-graph` skill). Start
that server, open the Review view, and pick a review from the queue:

```bash
node "$SKILLS_DIR"/serve-learning-graph/scripts/server.js --db learning.db --goal <goal-id-or-prefix> --user default --port 8765
```

`$SKILLS_DIR` is the directory that contains the installed skills — `skills/`
when working inside this repository, or the agent's skill directory (e.g.
`~/.claude/skills`) after `npx skills add`. `serve-learning-graph` is always a
sibling of this skill and must be installed alongside it.

Then open `http://127.0.0.1:<port>/?view=review`, choose a review (which opens
`?view=review&review=<review-id>`), review the context, and start the re-exam.

The same logic is available headlessly through these actions:

- `POST /api/review/start` — body `{ user, review_id? | node? }`. Returns the
  node, review round/schedule, historical errors, and mnemonic context.
- Reuse the Exam API for the re-exam: `POST /api/exams/start` (with the review's
  node) and `POST /api/exams/<exam-id>/questions/<question-id>/answer`.
- `POST /api/review/<review-id>/finish` — body `{ user, exam_id, question_meta? }`.
  Delegates the exam result to the Exam finalizer, completes the current review,
  and reschedules. Returns `{ passed, total_score, next_review_days, ... }`.

## Rules

- Always pass an explicit `user` and a review id (or a node id for a manual
  review with no schedule).
- The re-exam reuses the Exam primitives; review only owns the entry, the
  priming context, and the completion/reschedule logic.
- On a failed re-exam the node is rescheduled (the exam finalizer only schedules
  on pass), so reviews never silently drop out of rotation.
- The service reuses `agents.reviewer`, `agents.mnemonic`, the exam service, and
  `graph.dag`, and adds no DB tables.

## Data plane vs. tool

The GUI host (`serve-learning-graph`) is LLM-free. Its `review_queue` /
`review_start` actions stay in the browser (queue + context are pure data), but
the re-exam itself (question generation + scoring) and `review_finish` return an
`agent_required` hand-off pointing here — the re-exam reuses the exam flow, which
is LLM work (see `docs/16-web-data-plane-handoff.md`). Run this skill in the tool
to take the review, then hand the learner a deep link back to the web host:

```bash
PYTHONPATH="$SKILLS_DIR"/serve-learning-graph/_core python -m src.infrastructure.web_link --view review --node <node-id-or-prefix>
# (inside this repository, plain `python -m src.infrastructure.web_link` works too)
```

(The `serve-learning-graph` host must be running; the link uses its live URL.)

## Scripts

- `scripts/workflow_api.py` (in `serve-learning-graph`) keeps `review_queue` /
  `review_start` as data actions and hands `review_finish` back via
  `agent_required`.
- `src/services/review.py` holds the stateless review-start service.
- `src/infrastructure/web_link.py` builds the web deep link from the serve sidecar URL.

## Requirements

- Install `serve-learning-graph` and `exam-start` alongside this skill: the
  re-exam reuses the exam primitives, and the web host serves the Review view.
- LLM steps run in the agent (this tool); API keys come from environment
  variables (`LLM_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`).
