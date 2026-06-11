"""
Entry point for the Learning Graph Engine CLI.

The CLI is a pure data plane (doc 19): init / inspect / manage already-generated
data, never calling an LLM. Generation lives in skills (run in Claude Code /
Codex): /decompose-learning-goal, /goal-assess, /learn-start, /exam-start,
/review-start, /review-list.

Usage (data-plane commands):
    python main.py --help
    python main.py init
    python main.py goal list
    python main.py goal remove <goal-id>
    python main.py goal export <goal-id>
    python main.py goal tree <goal-id>
    python main.py goal nodes <goal-id>
    python main.py learn progress <node-id>
    python main.py exam review <exam-id>
    python main.py errors list
    python main.py review list
    python main.py status
"""

from src.interface.entrypoints import run_main

if __name__ == "__main__":
    run_main()
