"""
File: agents/reviewer.py

Purpose:
    Ebbinghaus spaced-repetition review execution for mastered knowledge nodes.
    Manages the review queue, displays historical errors, and runs a full re-exam
    to update stability and schedule the next review.

Responsibilities:
    - Build and sort the review queue (critical > overdue > today)
    - Display historical error-notebook entries before the review exam
    - Run a full re-exam via examiner.run_exam_loop
    - After the exam: update stability, complete the review, and schedule next review

What this file does NOT do:
    - Teaching / Socratic dialogue (that lives in agents/teacher.py)
    - First-time exam (that lives in agents/examiner.py)
    - DB schema or migrations (that lives in db/database.py)
    - Ebbinghaus math (that lives in graph/dag.py)

Key Design Decisions:
    - get_review_queue() returns all pending reviews (not just "due today") so the
      CLI can show the full upcoming schedule. The "due now" slice is filtered by
      the caller (or via get_due_reviews from the DB).
    - Priority order: critical strictness > overdue (scheduled_at < now) > today.
      Within each tier, ordered by scheduled_at ASC so the most overdue comes first.
    - run_review_loop() always shows historical errors BEFORE starting the re-exam,
      so the learner is primed on their weak points.
    - After the exam, the old review entry is completed and a NEW one is created
      (whether the exam passed or not) so the schedule always advances.

Inputs:
    - node_id (optional): target a specific review; omit to pick the highest-priority one
    - user_id: learner identifier

Outputs:
    - Updated user_knowledge_state (stability, raw_score, status)
    - Completed review_schedule row
    - New review_schedule entry for next round
    - Error notebook rows for new wrong answers (written by examiner._finalize_exam)
"""

from datetime import datetime, timezone
from typing import Optional

from src.db import database as db
from src.graph import dag as dag_utils
from src.logger import get_logger

log = get_logger(__name__)


# ── Review Queue ───────────────────────────────────────────────────────────────

def get_review_queue(user_id: str = "default") -> list[dict]:
    """
    Return all pending reviews sorted by priority:
        1. critical strictness nodes (highest urgency)
        2. overdue (scheduled_at < now)
        3. scheduled today

    Within each tier, sorted by scheduled_at ASC.

    Returns a list of dicts (review_schedule rows joined with node info).
    """
    now = datetime.now(timezone.utc).isoformat()

    with db.get_connection() as conn:
        rows = conn.execute(
            """SELECT r.*, n.title AS node_title, n.strictness_level,
                      n.mastery_threshold, n.description
               FROM review_schedule r
               JOIN knowledge_nodes n ON n.id = r.node_id
               WHERE r.user_id = ? AND r.status = 'pending'
               ORDER BY r.scheduled_at ASC""",
            (user_id,),
        ).fetchall()

    reviews = [dict(r) for r in rows]

    def _priority(rev: dict) -> tuple:
        # Lower tuple → higher priority
        is_critical = 0 if rev.get("strictness_level") == "critical" else 1
        is_overdue  = 0 if rev.get("scheduled_at", "") <= now else 1
        return (is_critical, is_overdue, rev.get("scheduled_at", ""))

    reviews.sort(key=_priority)
    return reviews


# ── Review Loop ────────────────────────────────────────────────────────────────

def run_review_loop(
    node_id: Optional[str] = None,
    user_id: str = "default",
    model: Optional[str] = None,
    console=None,
) -> dict:
    """
    Execute one review session:

    1. Find the target review (specific node or highest-priority from queue).
    2. Show the node's historical error-notebook entries.
    3. Run a full exam via examiner.run_exam_loop.
    4. Complete the old review record and create a new one (next interval).

    Returns a summary dict:
        {
            "node_id": str,
            "node_title": str,
            "review_id": str,
            "passed": bool,
            "total_score": float,
            "next_review_days": int,
        }
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    if console is None:
        console = Console()

    # ── 1. Find the review record ──────────────────────────────────────────────
    review: Optional[dict] = None

    if node_id:
        # Look for a pending review for this specific node
        with db.get_connection() as conn:
            row = conn.execute(
                """SELECT r.*, n.title AS node_title, n.strictness_level,
                          n.mastery_threshold
                   FROM review_schedule r
                   JOIN knowledge_nodes n ON n.id = r.node_id
                   WHERE r.user_id = ? AND r.node_id = ? AND r.status = 'pending'
                   ORDER BY r.scheduled_at ASC LIMIT 1""",
                (user_id, node_id),
            ).fetchone()
        if row:
            review = dict(row)
        else:
            # No scheduled review — still allow a manual review
            node = db.get_node(node_id)
            if not node:
                console.print(f"[red]找不到节点 ID: {node_id}[/red]")
                return {}
            log.info("Manual review (no pending schedule) for node '%s'", node["title"])
    else:
        queue = get_review_queue(user_id=user_id)
        if not queue:
            console.print("[green]✓ 当前没有待复习的节点。[/green]")
            return {}
        review = queue[0]
        node_id = review["node_id"]

    # Ensure we have a node object
    node = db.get_node(node_id)
    if not node:
        console.print(f"[red]找不到节点 ID: {node_id}[/red]")
        return {}

    node_title = node["title"]
    review_round = review["review_round"] if review else 1

    console.rule(f"[bold yellow]🔁 复习：{node_title}[/bold yellow]")
    if review:
        scheduled = (review.get("scheduled_at") or "")[:10]
        console.print(
            f"[dim]第 {review_round} 轮复习，计划日期：{scheduled}，"
            f"严格度：{node.get('strictness_level', 'standard')}[/dim]\n"
        )

    # ── 2. Show historical errors ──────────────────────────────────────────────
    errors = db.list_errors(user_id=user_id, node_id=node_id)
    if errors:
        console.print(f"[bold red]📖 历史错题（共 {len(errors)} 条）[/bold red]")
        table = Table(show_header=True, header_style="bold red", box=None)
        table.add_column("题目", min_width=35)
        table.add_column("错误类型", width=16)
        table.add_column("上次回答", min_width=25)
        for e in errors[:10]:  # show at most 10
            table.add_row(
                e["question"][:60] + ("..." if len(e["question"]) > 60 else ""),
                e.get("error_type") or "—",
                (e.get("user_answer") or "（无记录）")[:50],
            )
        console.print(table)
        if len(errors) > 10:
            console.print(f"[dim]... 还有 {len(errors) - 10} 条错题[/dim]")
        console.print()
    else:
        console.print("[dim]此节点没有历史错题记录。[/dim]\n")

    console.print("[bold]即将开始复习考试。请做好准备。[/bold]")
    console.print()

    # ── 3. Run full exam ───────────────────────────────────────────────────────
    from src.agents.examiner import run_exam_loop
    summary = run_exam_loop(
        node_id=node_id,
        user_id=user_id,
        model=model,
        console=console,
    )

    if not summary:
        # Exam was aborted or failed to start
        log.warning("Review exam aborted for node '%s'", node_title)
        return {}

    passed = summary.get("passed", False)
    total_score = summary.get("total_score", 0.0)

    # ── 4. Complete old review entry + schedule next review ────────────────────
    # Note: _finalize_exam already creates the next review_schedule row if passed.
    # For a failed review we also want to complete the old record (and it was already
    # handled by _finalize_exam calling create_review only on pass).
    # We need to complete the *current* pending review record regardless of pass/fail.
    if review:
        threshold = node.get("mastery_threshold", 0.80)
        next_round = review_round + 1
        interval_days = dag_utils.next_review_interval(next_round, total_score, threshold)
        db.complete_review(
            review_id=review["id"],
            score=total_score,
            next_interval_days=interval_days,
        )
        log.info(
            "Review %s completed: passed=%s score=%.2f next_interval=%dd",
            review["id"], passed, total_score, interval_days,
        )

        if not passed:
            # If exam failed, _finalize_exam did NOT create a new review;
            # create one with a shortened interval so the node stays in rotation.
            from datetime import timedelta
            next_review_date = (
                datetime.now(timezone.utc) + timedelta(days=interval_days)
            ).isoformat()
            db.create_review(
                node_id=node_id,
                scheduled_at=next_review_date,
                review_round=next_round,
                user_id=user_id,
            )
            log.info(
                "Failed review — rescheduled node '%s' in %d days", node_title, interval_days
            )

    result = {
        "node_id": node_id,
        "node_title": node_title,
        "review_id": review["id"] if review else None,
        "passed": passed,
        "total_score": total_score,
        "next_review_days": (
            summary.get("interval_days") if passed else (
                dag_utils.next_review_interval(
                    review_round + 1 if review else 1, total_score,
                    node.get("mastery_threshold", 0.80)
                ) if review else None
            )
        ),
    }

    return result
