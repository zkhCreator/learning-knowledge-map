---
name: goal-assess
description: Run an initial knowledge assessment for a learning goal so the path skips already-known content. Use when a user wants to assess, probe, or gauge what they already know about a goal before learning. Writes user_knowledge_state and does not auto-start learning.
---

# Goal Assess

Use this skill to run the initial assessment for an existing learning goal. It
adaptively probes a small number of atomic nodes, scores the user's free-text
answers, and records knowledge state so later learning can skip mastered
content.

## Purpose

- Determine which nodes the user already knows from a few adaptive probe
  questions, instead of teaching everything from scratch.
- Persist durable results to `user_knowledge_state` (mastered / learning /
  unknown), propagating mastery up prerequisites and unknown down dependents.
- Produce a summary with mastered/unknown counts and a recommended start node.

This skill does NOT auto-start learning. The summary only *shows* a suggested
next node; the user chooses whether to enter the Learn flow.

## How to invoke

This assessment runs in the GUI workflow host (the `serve-learning-graph`
skill). Start that server and open the Assess view:

```bash
node skills/serve-learning-graph/scripts/server.js --db data/learning.db --goal <goal-id-or-prefix> --user default --port 8765
```

Then open `http://127.0.0.1:<port>/?view=assess` (optionally `&goal=<goal-id>`).

The flow:

1. Pick a goal and a self-report level (1-5).
2. The server returns one probe question.
3. Submit a short answer; the server scores it and writes
   `user_knowledge_state`.
4. The server returns the next probe, or a final summary.

The same logic is available headlessly through two API actions for automation:

- `POST /api/goals/<goal>/assessment/start` — body `{ user, self_report }`.
- `POST /api/goals/<goal>/assessment/answer` — body `{ user, self_report,
  history, probe, user_answer }`.

The assessment is stateless on the server: each call carries the in-flight
`history` and `probe` (including the probe's `expected_answer`) in the
request/response cycle. Only durable state is written to the database.

## Rules

- Always pass an explicit `user` and goal id/prefix.
- The service writes `user_knowledge_state` only; it adds no DB tables.
- It never enters the Learn/Exam/Review flow automatically.
- Probe-question generation and answer scoring use the project LLM client; an
  `.env` with model keys is required to run against a real database.

## Data plane vs. tool

The GUI host (`serve-learning-graph`) is LLM-free: its `assessment_start` /
`assessment_answer` actions return an `agent_required` hand-off pointing here
rather than probing/scoring in the browser (see `docs/16-web-data-plane-handoff.md`).
Run this skill in the tool to do the assessment, then hand the learner a deep
link back to the web host:

```bash
python -m src.infrastructure.web_link --view assess --goal <goal-id-or-prefix>
```

(The `serve-learning-graph` host must be running; the link uses its live URL.)

## Scripts

- `scripts/workflow_api.py` (in `serve-learning-graph`) hands the
  `assessment_start` / `assessment_answer` actions back via `agent_required`.
- `src/services/assessment.py` holds the stateless assessment service.
- `src/infrastructure/web_link.py` builds the web deep link from the serve sidecar URL.
