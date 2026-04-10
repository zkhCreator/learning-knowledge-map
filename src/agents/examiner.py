"""
File: agents/examiner.py

Purpose:
    Exam generation, interactive exam loop, answer scoring, and error notebook
    population for individual knowledge nodes.

Responsibilities:
    - Generate exam questions based on the node's learning outline
    - Run an interactive CLI exam (one question at a time)
    - Score each answer with error attribution via the Reverse Agent
    - Compute final exam score and determine pass / fail
    - Update user_knowledge_state and create Ebbinghaus review schedule entry
    - Write failed questions to the error_notebook table

What this file does NOT do:
    - Teaching / dialogue (that lives in agents/teacher.py)
    - CLI formatting beyond the interactive exam loop
    - Ebbinghaus math (that lives in graph/dag.py)

Key Design Decisions:
    - Questions are generated in one batch before the exam starts (not on-the-fly),
      so the question set is consistent and can be reviewed later.
    - Scoring happens immediately after each answer (real-time feedback).
    - Error notebook entries are only written for questions where score < 0.6.
    - Exam questions reference outline sections via source_section index so errors
      can be traced back to specific learning material.

Inputs:
    - node_id: the knowledge_nodes row being examined
    - user_id: learner identifier

Outputs:
    - exam_attempts + exam_questions rows in DB
    - error_notebook rows for wrong answers
    - Updated user_knowledge_state (status, raw_score, stability)
    - New review_schedule entry (round 1) if exam passed
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from src import config
from src.agents import client as llm
from src.db import database as db
from src.graph import dag as dag_utils
from src.logger import get_logger

log = get_logger(__name__)


# ── System Prompts ─────────────────────────────────────────────────────────────

QUESTION_GEN_SYSTEM = """\
你是一名专业的考题设计师。你的任务是为一个知识点生成考试题目。

出题规则：
1. 主体题目（每个大纲小节至少 1 题）
2. 扩展题不超过总题数的 20%（考查关联知识，但不偏离主题太远）
3. 题型混合：选择题（multiple_choice）、简答题（short_answer）、情境应用题（scenario）、辨析题（distinction）
4. critical 严格度节点：增加 1-2 道易混淆的陷阱题（辨析题）
5. 每题的 expected_answer 必须详尽准确，这是评分的标准答案

输出规则：
- 以 JSON 格式输出，包含 questions 数组
- 每道题包含：question_type, question, options(仅选择题), expected_answer, source_section(大纲小节序号，扩展题为null), is_expansion

{
  "questions": [
    {
      "question_type": "short_answer",
      "question": "...",
      "options": null,
      "expected_answer": "...",
      "source_section": 1,
      "is_expansion": false
    }
  ]
}
""".strip()

SCORE_SYSTEM = """\
你是一名专业的答案评分专家。你的任务是对学生的答案进行评分，并分析错误原因。

评分规则：
- score: 0.0 到 1.0 之间的浮点数
  - 1.0：完全正确，覆盖所有关键点
  - 0.7-0.9：基本正确，有小的遗漏或表述不够精确
  - 0.4-0.6：部分正确，理解了大方向但缺少关键内容
  - 0.0-0.3：错误或严重遗漏

- error_type（仅当 score < 0.6 时）：
  - "memory_confusion": 记忆混淆（记成了另一个概念）
  - "boundary_unclear": 概念边界不清（大方向对但细节错）
  - "fundamental_misunderstanding": 根本性误解（底层逻辑理解反了）
  - "incomplete": 不完整（知道一部分但遗漏关键点）

输出规则：
- 以 JSON 格式输出
- score: 浮点数
- error_type: 字符串或 null
- explanation: 评分说明（为什么得这个分，错在哪里）
- related_concepts: 与错误相关的知识点名称列表（可以帮助复习）

{
  "score": 0.8,
  "error_type": null,
  "explanation": "回答覆盖了核心概念，但遗漏了...",
  "related_concepts": []
}
""".strip()


# ── Question Generation ────────────────────────────────────────────────────────

def generate_questions(
    node: dict,
    outline_sections: list[dict],
    model: Optional[str] = None,
) -> list[dict]:
    """
    Generate exam questions based on the node and its outline sections.

    Returns a list of question dicts (not yet persisted to DB).
    """
    strictness = node.get("strictness_level", "standard")

    outline_text = "\n".join(
        f"§{s['index']} {s['title']}: {s.get('content', '')[:300]}"
        for s in outline_sections
    )

    user_prompt = (
        f"## 知识点\n**{node['title']}**\n"
        f"描述：{node.get('description', '')}\n"
        f"严格度：{strictness}\n\n"
        f"## 学习大纲\n{outline_text}\n\n"
        f"请生成考试题目。"
        + ("\n⚠️ 这是 critical 严格度节点，请额外增加 1-2 道辨析/陷阱题。" if strictness == "critical" else "")
    )

    log.info("Question Gen Agent: generating questions for node '%s' (%s)", node["title"], strictness)
    result = llm.call_json(QUESTION_GEN_SYSTEM, user_prompt, max_tokens=4096, model=model)

    questions: list[dict] = []
    if isinstance(result, dict):
        questions = result.get("questions", [])
    elif isinstance(result, list):
        questions = result

    if not questions:
        raise ValueError("Question generation returned empty question list.")

    # Normalise fields
    for q in questions:
        q.setdefault("question_type", "short_answer")
        q.setdefault("options", None)
        q.setdefault("source_section", None)
        q.setdefault("is_expansion", False)

    log.info("Generated %d questions for node '%s'", len(questions), node["title"])
    return questions


# ── Answer Scoring ─────────────────────────────────────────────────────────────

def score_answer(
    question: str,
    expected_answer: str,
    user_answer: str,
    strictness: str = "standard",
    model: Optional[str] = None,
) -> dict:
    """
    Score a single answer using the Reverse Agent.

    Returns dict with: score, error_type, explanation, related_concepts.
    """
    user_prompt = (
        f"## 问题\n{question}\n\n"
        f"## 标准答案\n{expected_answer}\n\n"
        f"## 学生的回答\n{user_answer}\n\n"
        f"严格度：{strictness}（{'请适当从严评分' if strictness == 'critical' else '正常评分'}）"
    )

    log.debug("Scoring answer for question: %s", question[:80])
    try:
        result = llm.call_json(SCORE_SYSTEM, user_prompt, max_tokens=1024, model=model)
        if not isinstance(result, dict):
            raise ValueError("Unexpected score response type")
    except Exception as e:
        log.error("Scoring failed: %s — using default score 0.0", e)
        return {"score": 0.0, "error_type": "incomplete", "explanation": f"评分失败: {e}", "related_concepts": []}

    return {
        "score": float(result.get("score", 0.0)),
        "error_type": result.get("error_type"),
        "explanation": result.get("explanation", ""),
        "related_concepts": result.get("related_concepts", []),
    }


# ── Exam Finalisation ──────────────────────────────────────────────────────────

def _finalize_exam(
    exam_id: str,
    node: dict,
    scored_questions: list[dict],  # exam_questions rows with score filled
    user_id: str = "default",
) -> dict:
    """
    Compute final score, update user state, create review schedule, write error notebook.

    Returns a summary dict with: total_score, passed, weak_sections.
    """
    if not scored_questions:
        return {"total_score": 0.0, "passed": False, "weak_sections": []}

    scores = [q.get("score") for q in scored_questions if q.get("score") is not None]
    total_score = sum(scores) / len(scores) if scores else 0.0

    threshold = node.get("mastery_threshold", config.MASTERY_THRESHOLDS.get(
        node.get("strictness_level", "standard"), 0.80
    ))
    passed = total_score >= threshold

    db.finish_exam(exam_id, total_score=total_score, passed=passed)
    log.info("Exam %s finalised: score=%.2f passed=%s threshold=%.2f",
             exam_id, total_score, passed, threshold)

    # ── Update user knowledge state ────────────────────────────────────────────
    now_str = datetime.now(timezone.utc).isoformat()
    state = db.get_state(node["id"], user_id) or {}
    old_stability = state.get("stability", 1.0)
    old_review_count = state.get("review_count", 0)

    new_stability = dag_utils.next_stability(old_stability, total_score, threshold)
    new_status = "mastered" if passed else "learning"
    new_review_count = old_review_count + 1

    # Next review date (round 1 for new mastery, or adjusted for re-exam)
    review_round = new_review_count
    interval_days = dag_utils.next_review_interval(review_round, total_score, threshold)
    next_review_date = (datetime.now(timezone.utc) + timedelta(days=interval_days)).isoformat()

    db.upsert_state(
        node_id=node["id"],
        status=new_status,
        raw_score=total_score,
        stability=new_stability,
        last_reviewed=now_str,
        next_review=next_review_date,
        review_count=new_review_count,
        user_id=user_id,
    )

    # ── Create review schedule entry ───────────────────────────────────────────
    if passed:
        db.create_review(
            node_id=node["id"],
            scheduled_at=next_review_date,
            review_round=review_round,
            user_id=user_id,
        )
        log.info("Review scheduled for node %s in %d days", node["id"], interval_days)

    # ── Write error notebook ───────────────────────────────────────────────────
    weak_sections: list[str] = []
    for q in scored_questions:
        q_score = q.get("score")
        if q_score is None or q_score >= 0.6:
            continue

        # Find outline section title
        source_section_title = ""
        source_idx = q.get("source_section")
        if source_idx:
            source_section_title = f"§{source_idx}"

        # Collect related node titles from concepts (best-effort lookup)
        related_concepts = q.get("_related_concepts", [])
        related_node_ids: list[str] = []
        related_node_titles: list[str] = related_concepts

        db.add_error(
            node_id=node["id"],
            exam_id=exam_id,
            question_id=q["id"],
            source_section_title=source_section_title or q.get("source_section_title", ""),
            error_type=q.get("_error_type") or "incomplete",
            question=q["question"],
            user_answer=q.get("user_answer", ""),
            correct_answer=q["expected_answer"],
            explanation=q.get("_explanation", ""),
            related_node_ids=related_node_ids,
            related_node_titles=related_node_titles,
            user_id=user_id,
        )
        weak_sections.append(source_section_title or "扩展题")

    log.info("Error notebook: %d entries written for exam %s", len(weak_sections), exam_id)

    return {
        "total_score": total_score,
        "passed": passed,
        "threshold": threshold,
        "interval_days": interval_days if passed else None,
        "next_review": next_review_date if passed else None,
        "weak_sections": list(set(weak_sections)),
    }


# ── Interactive Exam REPL ──────────────────────────────────────────────────────

def run_exam_loop(
    node_id: str,
    user_id: str = "default",
    model: Optional[str] = None,
    console=None,
    skip_outline_gen: bool = False,
) -> dict:
    """
    Run the interactive exam for a node in the terminal.

    Returns the exam summary dict from _finalize_exam.
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.rule import Rule

    if console is None:
        console = Console()

    node = db.get_node(node_id)
    if not node:
        console.print(f"[red]找不到节点 ID: {node_id}[/red]")
        return {}

    console.rule(f"[bold yellow]📝 考试：{node['title']}[/bold yellow]")

    # Load or generate outline
    outline = db.get_outline(node_id, user_id)
    if not outline:
        if skip_outline_gen:
            console.print("[yellow]未找到大纲，将基于节点描述直接出题。[/yellow]")
            outline_sections = []
        else:
            console.print("[dim]未找到大纲，正在快速生成...[/dim]")
            try:
                from src.agents.teacher import generate_outline
                outline = generate_outline(
                    node_id=node_id,
                    user_id=user_id,
                    model=model,
                    progress_cb=lambda msg: console.print(f"[dim]{msg}[/dim]"),
                )
                outline_sections = outline["sections"]
            except Exception as e:
                console.print(f"[yellow]大纲生成失败（{e}），将基于节点描述直接出题。[/yellow]")
                outline_sections = []
    else:
        outline_sections = outline["sections"]

    # Generate questions
    console.print("[dim]正在生成考题...[/dim]")
    try:
        questions_data = generate_questions(node, outline_sections, model=model)
    except Exception as e:
        console.print(f"[red]考题生成失败：{e}[/red]")
        return {}

    total_q = len(questions_data)
    console.print(f"[green]✓[/green] 共生成 {total_q} 道题目\n")

    # Create exam record
    outline_id = outline["id"] if outline else None
    exam = db.create_exam(node_id=node_id, outline_id=outline_id, user_id=user_id)

    # Persist questions (unanswered)
    question_rows: list[dict] = []
    for qd in questions_data:
        q_row = db.add_exam_question(
            exam_id=exam["id"],
            question=qd["question"],
            expected_answer=qd["expected_answer"],
            question_type=qd.get("question_type", "short_answer"),
            options=qd.get("options"),
            source_section=qd.get("source_section"),
            is_expansion=bool(qd.get("is_expansion", False)),
        )
        question_rows.append(q_row)

    # ── Question loop ──────────────────────────────────────────────────────────
    console.print("[dim]输入 /exit 放弃考试并退出[/dim]\n")
    scored_rows: list[dict] = []
    aborted = False

    for i, (q_row, q_data) in enumerate(zip(question_rows, questions_data), 1):
        q_type = q_data.get("question_type", "short_answer")
        type_label = {
            "multiple_choice": "选择题",
            "short_answer": "简答题",
            "scenario": "情境题",
            "distinction": "辨析题",
        }.get(q_type, q_type)

        expansion_tag = " [dim][扩展][/dim]" if q_data.get("is_expansion") else ""
        console.print(Panel(
            f"[bold]第 {i}/{total_q} 题  [{type_label}]{expansion_tag}[/bold]\n\n"
            + q_data["question"]
            + (_format_options(q_data.get("options")) if q_data.get("options") else ""),
            border_style="yellow",
        ))

        # Get answer
        try:
            user_answer = input("你的回答：").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]已中断考试。[/yellow]")
            aborted = True
            break

        if user_answer.lower() in ("/exit", "/quit"):
            console.print("[yellow]已放弃本次考试。[/yellow]")
            aborted = True
            break

        if not user_answer:
            user_answer = "（未作答）"

        # Score
        console.print("[dim]正在评分...[/dim]")
        scoring = score_answer(
            question=q_data["question"],
            expected_answer=q_data["expected_answer"],
            user_answer=user_answer,
            strictness=node.get("strictness_level", "standard"),
            model=model,
        )

        score_val = scoring["score"]
        db.answer_exam_question(q_row["id"], user_answer=user_answer, score=score_val)

        # Show immediate feedback
        score_color = "green" if score_val >= 0.8 else ("yellow" if score_val >= 0.5 else "red")
        score_bar = _score_bar(score_val)
        console.print(f"\n[{score_color}]{score_bar}  得分：{score_val:.0%}[/{score_color}]")
        console.print(f"[dim]{scoring['explanation']}[/dim]")
        if score_val < 0.6:
            console.print(f"[dim]参考答案：{q_data['expected_answer'][:300]}[/dim]")
        console.print()

        # Accumulate for finalisation
        enriched = dict(q_row)
        enriched["score"] = score_val
        enriched["user_answer"] = user_answer
        enriched["_error_type"] = scoring.get("error_type")
        enriched["_explanation"] = scoring.get("explanation", "")
        enriched["_related_concepts"] = scoring.get("related_concepts", [])
        scored_rows.append(enriched)

    if aborted:
        console.print("[yellow]考试未完成，成绩不计入。[/yellow]")
        return {}

    # ── Finalise ───────────────────────────────────────────────────────────────
    console.rule("[bold]考试结果[/bold]")
    summary = _finalize_exam(exam["id"], node, scored_rows, user_id=user_id)

    _print_exam_summary(console, node, summary, scored_rows)
    return summary


def _format_options(options) -> str:
    if not options:
        return ""
    lines = ["\n"]
    labels = ["A", "B", "C", "D", "E"]
    for i, opt in enumerate(options):
        label = labels[i] if i < len(labels) else str(i + 1)
        lines.append(f"{label}. {opt}")
    return "\n".join(lines)


def _score_bar(score: float) -> str:
    filled = int(score * 10)
    return "■" * filled + "□" * (10 - filled)


def _print_exam_summary(console, node: dict, summary: dict, scored_rows: list[dict]):
    """Print the full exam result summary."""
    from rich.table import Table

    total = summary.get("total_score", 0.0)
    passed = summary.get("passed", False)
    threshold = summary.get("threshold", 0.8)
    weak = summary.get("weak_sections", [])

    # Score panel
    result_color = "green" if passed else "red"
    result_icon = "🎉" if passed else "❌"
    console.print(Panel(
        f"{result_icon} [bold {result_color}]{'通过' if passed else '未通过'}[/bold {result_color}]\n\n"
        f"综合得分：[bold]{total:.0%}[/bold]  （通过线：{threshold:.0%}）\n"
        + (f"下次复习：{summary.get('interval_days')} 天后" if passed else
           "建议重新学习后再考试"),
        border_style=result_color,
    ))

    # Question breakdown table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", width=3)
    table.add_column("题目", min_width=20)
    table.add_column("得分", width=6)
    table.add_column("错误类型", width=14)

    for i, q in enumerate(scored_rows, 1):
        score_val = q.get("score", 0.0)
        score_color = "green" if score_val >= 0.8 else ("yellow" if score_val >= 0.5 else "red")
        error_type = q.get("_error_type") or ("—" if score_val >= 0.6 else "incomplete")
        table.add_row(
            str(i),
            q["question"][:50] + ("..." if len(q["question"]) > 50 else ""),
            f"[{score_color}]{score_val:.0%}[/{score_color}]",
            error_type,
        )

    console.print(table)

    if weak:
        console.print(f"\n[yellow]薄弱章节：{', '.join(weak)}[/yellow]")
        console.print("[dim]这些章节的错题已记入错题本，使用 python main.py errors list 查看。[/dim]")

    if not passed:
        console.print(
            f"\n[dim]提示：使用 python main.py learn start {node['id'][:8]} 重新学习该节点。[/dim]"
        )
