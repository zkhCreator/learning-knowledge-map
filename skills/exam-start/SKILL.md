---
name: exam-start
description: Run a post-learning exam for a knowledge node. Use when a user wants to test, quiz, or examine their mastery of a specific node after studying it. Generates questions from the node outline, scores each answer, then finalizes — updating mastery state, writing the error notebook, and scheduling the next Ebbinghaus review on pass.
---

# Exam Start

Use this skill to examine a single atomic knowledge node after learning it. It
generates exam questions from the node's validated outline, scores each answer,
and finalizes the attempt: updating `user_knowledge_state`, writing weak answers
to the error notebook, and—on a passing score—scheduling the next Ebbinghaus
review.

## Purpose

- Verify mastery of one node at a time with outline-anchored questions
  (short-answer, scenario, distinction, and—for critical nodes—extra trap
  questions).
- Score each answer with the reverse-agent scorer and persist the score
  server-side; the per-question grade is authoritative in SQLite.
- Finalize the exam: compute the total from the DB-persisted scores, update the
  node's mastery state and stability, write error-notebook entries for weak
  answers, and create a `review_schedule` row when the node passes.

This skill is an independent entry. It is the destination the Learn flow only
*surfaces* at readiness — it is never auto-started by learning.

## Architecture: tool generates, web records

The GUI workflow host (`serve-learning-graph`) is a **data plane**: it never
calls the LLM. Question generation and scoring are LLM work, so they run **here,
in the tool**, while the web host only displays the exam and records raw answers.
See `docs/16-web-data-plane-handoff.md`.

The flow has two tool steps with a web step in between:

1. **Generate (tool)** — create + persist the exam, then hand the learner a deep
   link to answer it in the web host.
2. **Answer (web)** — the learner opens the deep link, answers each question, and
   the host saves the raw answers (no score yet).
3. **Score (tool)** — score every recorded answer, finalize the attempt (mastery
   state, error notebook, review schedule on pass), and print a result deep link.

If the learner clicks a generation/scoring action in the web host, it returns an
`agent_required` hand-off telling them to run this skill — never a stack trace.

## How to invoke

Generate an exam for a node:

```bash
python skills/exam-start/scripts/exam_cli.py generate --db data/learning.db --node <node-id-or-prefix> --user default
```

This prints the `exam_id` and a deep link like `…/?view=exam&exam=<exam_id>`.
The learner opens it (the `serve-learning-graph` host must be running), answers,
and saves. Then score + finalize:

```bash
python skills/exam-start/scripts/exam_cli.py score --db data/learning.db --exam <exam_id> --user default
```

This scores each recorded answer, finalizes, and prints a result deep link.

## Rules

- Always pass an explicit `--user` and the node / exam id.
- `expected_answer` is persisted to SQLite for scoring and is NEVER sent to the
  browser; the web data-plane view (`exam_get`) omits it.
- The total score is recomputed from the DB-persisted per-question scores in
  `finish_exam`, so it cannot be inflated by the client.
- Generation/scoring run outside `LDG_WEB_MODE`; the web host sets that flag so
  any LLM call there is refused and handed back to this skill.
- The CLI reuses `agents.examiner` (question generation, scoring, finalization),
  `agents.teacher` (outline), and `src/services/exam.py`; it adds no DB tables.
- An `.env` with model keys is required to run generation/scoring against a real
  database.

## Scripts

- `scripts/exam_cli.py` — `generate` and `score` subcommands (this skill).
- `src/services/exam.py` — `start_exam` (generate), `record_answer` /
  `get_exam_view` (web data plane), `finish_exam` (finalize).
- `src/infrastructure/web_link.py` — builds the web deep link from the serve sidecar URL.
- `scripts/workflow_api.py` (in `serve-learning-graph`) exposes the data-plane
  `exam_get` / `exam_record_answer` actions and hands generation/scoring back via
  `agent_required`.
