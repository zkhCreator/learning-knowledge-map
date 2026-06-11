"""
File: src/interface/main.py

Purpose:
    Entry point for the learning system CLI — a pure DATA PLANE (doc 19).
    The CLI never calls an LLM: it initialises, inspects, and manages already
    generated data. Every generation step (decompose / assess / learn / exam /
    review) is handed off to its skill in Claude Code / Codex.

Responsibilities:
    - Parse commands and flags
    - Display already-generated data (graph, status, progress, results)
    - Delegate data access to data/ and domain/ modules

Available commands (all data-plane, no LLM):
    init                        Initialise the database
    goal list                   List all goals
    goal remove <id>            Remove a goal and all related learning data
    goal export <id>            Export a goal graph to a draw.io diagram
    goal tree <id>              Show the knowledge graph for a goal
    goal nodes <id>             List atomic nodes in learning order
    status                      Show today's learning status (due reviews + next node)
    learn progress <node-id>    Show outline coverage progress for a node
    exam review <exam-id>       Show exam result details
    errors list                 View error notebook entries
    review list                 Show the Ebbinghaus review queue (priority sorted)

Moved to skills (generation; not reachable from the CLI anymore — see doc 19):
    goal new        → /decompose-learning-goal
    goal assess     → /goal-assess
    learn start     → /learn-start
    learn chat      → /learn-start (resume)
    exam start      → /exam-start generate
    errors review   → /review-start
    review start    → /review-start

What this file does NOT do:
    - Business logic
    - Any LLM / agent call (generation lives in skills/)
"""

import typer
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint

app = typer.Typer(
    name="learn",
    help="🧠 Recursive Learning Graph Engine — CLI",
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console()

# Global verbose flag — set by --verbose on any command
_verbose: bool = False


def _setup_logging(verbose: bool = False):
    """Initialise logging. Call at the start of every command that does real work."""
    global _verbose
    _verbose = verbose
    from src.infrastructure.logger import setup as log_setup
    from src.infrastructure import config
    log_setup(verbose=verbose)
    if verbose:
        console.print(
            f"[dim]📋 Verbose mode ON — streaming DEBUG logs to stderr[/dim]\n"
            f"[dim]   (or run: tail -f {config.DB_PATH.parent}/learning.log)[/dim]\n"
        )
    else:
        from src.infrastructure import config
        console.print(
            f"[dim]📋 Logs → {config.DB_PATH.parent}/learning.log  "
            f"(add --verbose to also print here)[/dim]\n"
        )


# ── init ───────────────────────────────────────────────────────────────────────

@app.command()
def init(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="同时将 DEBUG 日志打印到终端"),
):
    """Initialise the SQLite database. Safe to run multiple times."""
    from src.infrastructure import config
    config.validate()
    _setup_logging(verbose)
    from src.data import database as db
    db.init_db()
    rprint(f"[green]✓[/green] 数据库初始化完成：{config.DB_PATH}")


# ── goal ───────────────────────────────────────────────────────────────────────

goal_app = typer.Typer(help="管理学习目标", no_args_is_help=True)
app.add_typer(goal_app, name="goal")


@goal_app.command("list")
def goal_list(user_id: str = typer.Option("default", "--user", "-u")):
    """列出所有学习目标。"""
    from src.data import database as db
    goals = db.list_goals(user_id=user_id)

    if not goals:
        rprint("[yellow]暂无学习目标。在 Claude Code / Codex 中运行 /decompose-learning-goal '...' 创建一个。[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("ID (前8位)", style="dim", width=10)
    table.add_column("目标", min_width=30)
    table.add_column("状态", width=12)
    table.add_column("创建时间", width=20)

    for g in goals:
        status_color = {
            "decomposing": "yellow",
            "active": "green",
            "completed": "blue",
        }.get(g["status"], "white")
        table.add_row(
            g["id"][:8],
            g["title"],
            f"[{status_color}]{g['status']}[/{status_color}]",
            g["created_at"][:19],
        )

    console.print(table)


@goal_app.command("remove")
def goal_remove(
    goal_id_prefix: str = typer.Argument(..., help="Goal ID 或前8位"),
    user_id: str = typer.Option("default", "--user", "-u", help="用户 ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="不询问，直接删除"),
):
    """删除目标及其关联的知识图谱、学习状态和复习计划。"""
    from src.data import database as db

    goal = _resolve_goal(goal_id_prefix, user_id=user_id)
    if not goal:
        return

    node_count = len(db.list_nodes_for_goal(goal["id"]))
    if not yes:
        confirmed = typer.confirm(
            f"确认删除目标 '{goal['title']}' ({goal['id'][:8]}) 吗？"
            f" 这会同时删除 {node_count} 个知识点及其关联状态/复习记录"
        )
        if not confirmed:
            console.print("[yellow]已取消删除。[/yellow]")
            return

    deleted = db.delete_goal(goal["id"])
    if not deleted["goals"]:
        console.print("[red]删除失败：目标不存在或已被删除。[/red]")
        raise typer.Exit(1)

    console.print(
        f"[green]✓[/green] 已删除目标 [bold]{goal['title']}[/bold] ({goal['id'][:8]})"
    )
    console.print(
        "[dim]"
        f"已删除 {deleted['nodes']} 个知识点、"
        f"{deleted['edges']} 条依赖边、"
        f"{deleted['states']} 条学习状态、"
        f"{deleted['reviews']} 条复习计划。"
        "[/dim]"
    )


@goal_app.command("export")
def goal_export(
    goal_id_prefix: str = typer.Argument(..., help="Goal ID 或前8位"),
    user_id: str = typer.Option("default", "--user", "-u", help="用户 ID"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="输出 .drawio 文件路径"),
    atomic_only: bool = typer.Option(False, "--atomic-only", help="只导出原子知识点"),
):
    """导出为 draw.io / diagrams.net 可直接打开的节点图。"""
    from src.domain import drawio

    goal = _resolve_goal(goal_id_prefix, user_id=user_id)
    if not goal:
        return

    output_path = output or str(drawio.default_drawio_path(goal))
    try:
        saved_path = drawio.export_goal_to_drawio(
            goal_id=goal["id"],
            output_path=output_path,
            user_id=user_id,
            atomic_only=atomic_only,
        )
    except ValueError as exc:
        console.print(f"[red]导出失败：{exc}[/red]")
        raise typer.Exit(1)

    console.print(
        f"[green]✓[/green] 已导出 draw.io 节点图：[bold]{saved_path}[/bold]"
    )
    console.print(
        "[dim]可在 draw.io / diagrams.net 中直接打开该 .drawio 文件。[/dim]"
    )


@goal_app.command("tree")
def goal_tree(
    goal_id_prefix: str = typer.Argument(..., help="Goal ID 或前8位"),
    user_id: str = typer.Option("default", "--user", "-u"),
):
    """以树状图展示目标的知识图谱结构。"""
    from src.domain import dag

    goal = _resolve_goal(goal_id_prefix, user_id=user_id)
    if not goal:
        return

    tree_str = dag.print_tree(goal["id"], user_id=user_id)
    console.print(Panel(tree_str, title="知识图谱", border_style="blue"))


@goal_app.command("nodes")
def goal_nodes(
    goal_id_prefix: str = typer.Argument(..., help="Goal ID 或前8位"),
    user_id: str = typer.Option("default", "--user", "-u"),
):
    """按学习顺序列出原子知识点（拓扑排序）。"""
    from src.data import database as db
    from src.domain import dag

    goal = _resolve_goal(goal_id_prefix, user_id=user_id)
    if not goal:
        return

    nodes = dag.topological_order(goal["id"])
    if not nodes:
        rprint("[yellow]暂无知识点，请先用 /decompose-learning-goal 拆解目标。[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("知识点", min_width=25)
    table.add_column("掌握度", width=8)
    table.add_column("状态", width=10)
    table.add_column("时间", width=6)
    table.add_column("领域", width=12)

    with db.get_connection() as conn:
        state_rows = conn.execute(
            "SELECT * FROM user_knowledge_state WHERE user_id = ?", (user_id,)
        ).fetchall()
    state_map = {r["node_id"]: dict(r) for r in state_rows}

    for i, node in enumerate(nodes, 1):
        state = state_map.get(node["id"])
        if state:
            em = dag.effective_mastery(
                state.get("raw_score", 0),
                state.get("stability", 1),
                state.get("last_reviewed"),
            )
            mastery_str = f"{em:.0%}"
            status_str = state.get("status", "unknown")
        else:
            mastery_str = "—"
            status_str = "unknown"

        status_color = {
            "mastered": "green",
            "learning": "yellow",
            "needs_review": "red",
            "unknown": "dim",
        }.get(status_str, "dim")

        table.add_row(
            str(i),
            node["title"],
            mastery_str,
            f"[{status_color}]{status_str}[/{status_color}]",
            f"{node.get('est_minutes', '?')} min",
            node.get("domain", "—"),
        )

    console.print(table)
    total_min = sum(n.get("est_minutes", 0) for n in nodes)
    console.print(f"\n共 {len(nodes)} 个知识点，预计总时间 {total_min} 分钟")


# ── status ─────────────────────────────────────────────────────────────────────

@app.command()
def status(
    user_id: str = typer.Option("default", "--user", "-u"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="同时将 DEBUG 日志打印到终端"),
):
    """显示今日学习状态：待复习节点 + 下一个待学节点。"""
    _setup_logging(verbose)
    from src.data import database as db

    console.rule("[bold blue]今日学习状态[/bold blue]")

    # Due reviews
    due = db.get_due_reviews(user_id=user_id)
    if due:
        console.print(f"\n[red]🔔 待复习：{len(due)} 个[/red]")
        for r in due[:5]:
            console.print(f"   • {r['node_title']}  [{r.get('strictness_level','standard')}]")
        if len(due) > 5:
            console.print(f"   ... 还有 {len(due)-5} 个")
        console.print(f"\n   [dim]在 Claude Code / Codex 中运行 /review-start 开始最紧急的复习[/dim]")
    else:
        console.print("\n[green]✓ 今日无待复习任务[/green]")

    # Next recommended node to learn
    from src.domain import dag as dag_module
    goals = db.list_goals(user_id=user_id)
    next_node = None
    for g in goals:
        ordered = dag_module.topological_order(g["id"])
        states = {s["node_id"]: s for s in db.list_states(user_id=user_id)}
        for n in ordered:
            state = states.get(n["id"])
            if not state or state.get("status") not in ("mastered",):
                next_node = n
                break
        if next_node:
            break

    if next_node:
        console.print(
            f"\n[bold cyan]📖 下一个建议学习节点：[/bold cyan] {next_node['title']}"
            f"  [dim]({next_node['id'][:8]})[/dim]"
        )
        console.print(
            f"   [dim]运行 /learn-start {next_node['id'][:8]} 开始学习（Claude Code / Codex）[/dim]"
        )

    # Goals summary
    if goals:
        console.print(f"\n📚 共 {len(goals)} 个学习目标")
        for g in goals:
            nodes = db.list_nodes_for_goal(g["id"], atomic_only=True)
            states_list = db.list_states(user_id=user_id)
            node_ids = {n["id"] for n in nodes}
            mastered = sum(
                1 for s in states_list
                if s["node_id"] in node_ids and s.get("status") == "mastered"
            )
            console.print(
                f"   [{g['status']}] {g['title'][:40]}  "
                f"{mastered}/{len(nodes)} 节点已掌握"
            )
    else:
        console.print(
            "\n[yellow]尚无学习目标。在 Claude Code / Codex 中运行 /decompose-learning-goal '...' 开始。[/yellow]"
        )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _resolve_goal(goal_id_prefix: str, user_id: str = "default") -> Optional[dict]:
    """Find a goal by full ID or prefix for the selected user."""
    from src.data import database as db
    goals = db.list_goals(user_id=user_id)
    matches = [g for g in goals if g["id"].startswith(goal_id_prefix)]
    if not matches:
        rprint(f"[red]找不到 Goal ID 以 '{goal_id_prefix}' 开头的目标[/red]")
        return None
    if len(matches) > 1:
        rprint(f"[red]找到多个匹配目标，请提供更长的 ID[/red]")
        return None
    return matches[0]


# ── learn ──────────────────────────────────────────────────────────────────────

learn_app = typer.Typer(help="节点级学习（大纲 + 苏格拉底对话）", no_args_is_help=True)
app.add_typer(learn_app, name="learn")


@learn_app.command("progress")
def learn_progress(
    node_id_prefix: str = typer.Argument(..., help="Node ID 或前8位"),
    user_id: str = typer.Option("default", "--user", "-u"),
):
    """显示某节点的大纲学习进度（哪些章节已覆盖）。"""
    from src.data import database as db

    node = _resolve_node(node_id_prefix, user_id=user_id)
    if not node:
        return

    outline = db.get_outline(node["id"], user_id)
    if not outline:
        rprint("[yellow]该节点尚无学习大纲，请先运行 learn start。[/yellow]")
        return

    session = db.get_active_session(node["id"], user_id)
    covered = session.get("covered_sections", []) if session else []
    sections = outline["sections"]
    total = len(sections)
    progress = len(covered) / total if total > 0 else 0.0

    console.rule(f"[bold]{node['title']}[/bold]")
    console.print(f"学习进度：[bold]{progress:.0%}[/bold]  ({len(covered)}/{total} 节)\n")

    from src.agents.teacher import _print_sections_detail
    _print_sections_detail(console, sections, covered)

    if session:
        console.print(f"[dim]会话状态：{session.get('status')}[/dim]")
    else:
        console.print("[dim]没有活跃的学习会话。[/dim]")


# ── exam ───────────────────────────────────────────────────────────────────────

exam_app = typer.Typer(help="节点考试", no_args_is_help=True)
app.add_typer(exam_app, name="exam")


@exam_app.command("review")
def exam_review(
    exam_id_prefix: str = typer.Argument(..., help="Exam ID 或前8位"),
    user_id: str = typer.Option("default", "--user", "-u"),
):
    """查看某次考试的结果详情（题目、答案、得分）。"""
    from src.data import database as db

    # Resolve exam by prefix via questions table
    with db.get_connection() as conn:
        row = conn.execute(
            """SELECT a.*, n.title as node_title
               FROM exam_attempts a
               JOIN knowledge_nodes n ON n.id = a.node_id
               WHERE a.user_id = ? AND a.id LIKE ?
               ORDER BY a.started_at DESC LIMIT 1""",
            (user_id, f"{exam_id_prefix}%"),
        ).fetchone()

    if not row:
        rprint(f"[red]找不到以 '{exam_id_prefix}' 开头的考试记录[/red]")
        raise typer.Exit(1)

    exam = dict(row)
    questions = db.get_exam_questions(exam["id"])

    console.rule(f"[bold]考试详情[/bold]")
    console.print(f"节点：[bold]{exam['node_title']}[/bold]")
    console.print(
        f"得分：[bold]{(exam.get('total_score') or 0):.0%}[/bold]  "
        f"{'[green]通过[/green]' if exam.get('passed') else '[red]未通过[/red]'}"
    )
    console.print(f"时间：{(exam.get('started_at') or '')[:19]}\n")

    from rich.table import Table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", width=3)
    table.add_column("题目", min_width=25)
    table.add_column("你的回答", min_width=20)
    table.add_column("得分", width=6)

    for i, q in enumerate(questions, 1):
        score_val = q.get("score") or 0.0
        color = "green" if score_val >= 0.8 else ("yellow" if score_val >= 0.5 else "red")
        table.add_row(
            str(i),
            q["question"][:60] + ("..." if len(q["question"]) > 60 else ""),
            (q.get("user_answer") or "（未作答）")[:50],
            f"[{color}]{score_val:.0%}[/{color}]",
        )

    console.print(table)


# ── errors ─────────────────────────────────────────────────────────────────────

errors_app = typer.Typer(help="错题本", no_args_is_help=True)
app.add_typer(errors_app, name="errors")


@errors_app.command("list")
def errors_list(
    node_id_prefix: Optional[str] = typer.Option(None, "--node", "-n", help="按节点过滤（前8位）"),
    user_id: str = typer.Option("default", "--user", "-u"),
    error_type: Optional[str] = typer.Option(None, "--type", "-t", help="按错误类型过滤"),
    limit: int = typer.Option(20, "--limit", "-l", help="显示条数"),
):
    """查看错题本。可按节点或错误类型过滤。"""
    from src.data import database as db

    node_id: Optional[str] = None
    if node_id_prefix:
        node = _resolve_node(node_id_prefix, user_id=user_id)
        if not node:
            return
        node_id = node["id"]

    errors = db.list_errors(user_id=user_id, node_id=node_id, error_type=error_type)
    if not errors:
        rprint("[yellow]错题本为空。[/yellow]")
        return

    errors = errors[:limit]
    console.rule(f"[bold red]错题本[/bold red]  ({len(errors)} 条)")

    from rich.table import Table
    table = Table(show_header=True, header_style="bold red")
    table.add_column("ID", style="dim", width=8)
    table.add_column("节点", width=18)
    table.add_column("问题", min_width=25)
    table.add_column("错误类型", width=16)
    table.add_column("时间", width=12)

    for e in errors:
        table.add_row(
            e["id"][:8],
            e.get("node_title", "")[:18],
            e["question"][:50] + ("..." if len(e["question"]) > 50 else ""),
            e.get("error_type", "—"),
            (e.get("created_at") or "")[:10],
        )

    console.print(table)
    console.print(
        f"\n[dim]在 Claude Code / Codex 中运行 /review-start <node-id> 重做某节点的历史错题[/dim]"
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _resolve_node(node_id_prefix: str, user_id: str = "default") -> Optional[dict]:
    """Find a node by full ID or prefix."""
    from src.data import database as db
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM knowledge_nodes WHERE id LIKE ? LIMIT 5",
            (f"{node_id_prefix}%",),
        ).fetchall()

    matches = [dict(r) for r in rows]
    if not matches:
        rprint(f"[red]找不到 Node ID 以 '{node_id_prefix}' 开头的知识点[/red]")
        return None
    if len(matches) > 1:
        rprint(f"[red]找到多个匹配节点，请提供更长的 ID[/red]")
        for m in matches:
            rprint(f"  {m['id'][:12]}  {m['title']}")
        return None
    return matches[0]


# ── review ─────────────────────────────────────────────────────────────────────

review_app = typer.Typer(help="Ebbinghaus 间隔复习", no_args_is_help=True)
app.add_typer(review_app, name="review")


@review_app.command("list")
def review_list(
    user_id: str = typer.Option("default", "--user", "-u"),
    all_pending: bool = typer.Option(False, "--all", "-a", help="显示所有待复习（含未来日期）"),
):
    """显示复习队列（按优先级排序：critical > 逾期 > 今日）。"""
    from src.agents.reviewer import get_review_queue
    from src.data import database as db

    queue = get_review_queue(user_id=user_id)
    if not queue:
        rprint("[green]✓ 没有待复习任务。[/green]")
        return

    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    if not all_pending:
        # Show only overdue + today (scheduled_at <= now + 1 day)
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        due_queue = [r for r in queue if r.get("scheduled_at", "") <= cutoff]
        if not due_queue:
            rprint(
                f"[green]✓ 今日无待复习任务。[/green] "
                f"[dim]共 {len(queue)} 条计划中，使用 --all 查看全部[/dim]"
            )
            return
        display = due_queue
    else:
        display = queue

    console.rule(f"[bold yellow]复习队列[/bold yellow]  ({len(display)} 条)")
    table = Table(show_header=True, header_style="bold yellow")
    table.add_column("#", width=3)
    table.add_column("节点", min_width=22)
    table.add_column("严格度", width=10)
    table.add_column("轮次", width=5)
    table.add_column("计划日期", width=12)
    table.add_column("状态", width=8)

    for i, rev in enumerate(display, 1):
        scheduled = (rev.get("scheduled_at") or "")[:10]
        is_overdue = rev.get("scheduled_at", "") <= now
        date_color = "red" if is_overdue else "yellow"
        strictness_color = "red" if rev.get("strictness_level") == "critical" else "white"
        status_str = "[red]逾期[/red]" if is_overdue else "待复习"
        table.add_row(
            str(i),
            rev.get("node_title", "")[:22],
            f"[{strictness_color}]{rev.get('strictness_level', 'standard')}[/{strictness_color}]",
            str(rev.get("review_round", 1)),
            f"[{date_color}]{scheduled}[/{date_color}]",
            status_str,
        )

    console.print(table)
    console.print(
        f"\n[dim]在 Claude Code / Codex 中运行 /review-start 开始最高优先级复习[/dim]"
    )


if __name__ == "__main__":
    app()
