---
name: learn-start
description: Run a per-node Socratic learning session for a knowledge node. Use when a user wants to learn, study, or work through a specific node. Generates or reuses an outline, resumes the session, tracks coverage/progress, and only surfaces an Exam entry at readiness without auto-starting the exam.
---

# Learn Start

Use this skill to learn a single atomic knowledge node through a Socratic
dialogue. It generates (or reuses) a validated learning outline, creates or
resumes a learning session, and tracks which outline sections have been covered
as the conversation progresses.

## Purpose

- Teach one node at a time via guided questioning anchored to a structured
  outline (3-8 sections), instead of dumping content.
- Persist the session, chat history, covered sections, and progress to SQLite
  so a session can be resumed later.
- Report `exam_ready` once progress reaches the completion threshold (>= 0.9),
  matching the CLI learning loop.

This skill does NOT auto-start an exam. At exam-readiness it only *shows* an
Exam entry/hint; the user decides whether to enter the Exam flow.

## How to invoke

Learning runs in the GUI workflow host (the `serve-learning-graph` skill). Start
that server and open the Learn view:

```bash
node "$SKILLS_DIR"/serve-learning-graph/scripts/server.js --db learning.db --goal <goal-id-or-prefix> --user default --port 8765
```

`$SKILLS_DIR` is the directory that contains the installed skills — `skills/`
when working inside this repository, or the agent's skill directory (e.g.
`~/.claude/skills`) after `npx skills add`. `serve-learning-graph` is always a
sibling of this skill and must be installed alongside it.

Then open `http://127.0.0.1:<port>/?view=learn` (optionally `&node=<node-id>`).

The flow:

1. Pick or paste a node id/prefix and prepare it.
2. The server generates or reuses the outline and creates/resumes the session.
3. Send a message; the server runs one Socratic turn, writes chat history, and
   updates covered sections + progress.
4. When progress reaches readiness, the UI surfaces an Exam entry — it never
   navigates there automatically.

The same logic is available headlessly through two API actions for automation:

- `POST /api/learn/<node>/prepare` — body `{ user, user_domains? }`. Returns the
  node, outline sections, session, chat history, progress, and `exam_ready`.
- `POST /api/learn/sessions/<session-id>/messages` — body `{ user, message }`.
  Returns `{ response, progress, covered, exam_ready }`.

## Rules

- Always pass an explicit `user` and a node id/prefix or session id.
- The service reuses `agents.teacher` (outline generation + Socratic turn) and
  adds no DB tables.
- It never enters the Exam/Review flow automatically; `exam_ready` is purely
  informational.
- Outline generation and the Socratic turn use the project LLM client; an
  `.env` with model keys is required to run against a real database.

## Data plane vs. tool

The GUI host (`serve-learning-graph`) is LLM-free: its `learn_prepare` /
`learn_message` actions return an `agent_required` hand-off pointing here rather
than generating outlines or Socratic turns in the browser (see
`docs/16-web-data-plane-handoff.md`). Run this skill in the tool to learn, then
hand the learner a deep link back to the web host:

```bash
PYTHONPATH="$SKILLS_DIR"/serve-learning-graph/_core python -m src.infrastructure.web_link --view learn --node <node-id-or-prefix>
# (inside this repository, plain `python -m src.infrastructure.web_link` works too)
```

(The `serve-learning-graph` host must be running; the link uses its live URL.)

## Scripts

- `scripts/workflow_api.py` (in `serve-learning-graph`) hands the
  `learn_prepare` / `learn_message` actions back via `agent_required`.
- `src/services/learning.py` holds the stateless learn-start service.
- `src/infrastructure/web_link.py` builds the web deep link from the serve sidecar URL.

## Requirements

- Install `serve-learning-graph` alongside this skill (it hosts the Learn view
  and the data-plane API).
- Outline generation / Socratic turns run in the agent (this tool); the web
  host stays LLM-free. API keys come from environment variables (`LLM_API_KEY`
  / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`).
- Web search for outline sections is optional (auto-skipped without
  `SEARCH_API_KEY`).
