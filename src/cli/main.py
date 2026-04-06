"""
File: cli/main.py

Purpose:
    Entry point for the learning system CLI.
    All user-facing commands live here.

Responsibilities:
    - Parse commands and flags
    - Display progress and results in a readable format
    - Delegate logic to agents/, db/, graph/ modules

Available commands:
    init                   Initialise the database
    goal new <title>       Create a new learning goal and decompose it
    goal list              List all goals
    goal tree <id>         Show the knowledge graph for a goal
    goal nodes <id>        List atomic nodes in learning order
    status                 Show today's learning status (due reviews + next node)

What this file does NOT do:
    - Business logic
    - Agent calls directly (routes through agents/decomposer.py etc.)
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
)
console = Console()

# Global verbose flag — set by --verbose on any command
_verbose: bool = False


def _setup_logging(verbose: bool = False):
    """Initialise logging. Call at the start of every command that does real work."""
    global _verbose
    _verbose = verbose
    from src.logger import setup as log_setup
    from src import config
    log_setup(verbose=verbose)
    if verbose:
        console.print(
            f"[dim]📋 Verbose mode ON — streaming DEBUG logs to stderr[/dim]\n"
            f"[dim]   (or run: tail -f {config.DB_PATH.parent}/learning.log)[/dim]\n"
        )
    else:
        from src import config
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
    from src import config
    config.validate()
    _setup_logging(verbose)
    from src.db import database as db
    db.init_db()
    rprint(f"[green]✓[/green] 数据库初始化完成：{config.DB_PATH}")


# ── goal ───────────────────────────────────────────────────────────────────────

goal_app = typer.Typer(help="管理学习目标")
app.add_typer(goal_app, name="goal")


@goal_app.command("new")
def goal_new(
    title: str = typer.Argument(..., help="学习目标，例如 '学会 Kubernetes 集群管理'"),
    domains: Optional[str] = typer.Option(
        None, "--domains", "-d",
        help="用户已知领域（逗号分隔），例如 'Linux,Python,数据库'"
    ),
    user_id: str = typer.Option("default", "--user", "-u", help="用户 ID"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="同时将 DEBUG 日志打印到终端"),
):
    """
    创建新的学习目标，并自动用双 Agent 递归拆解为知识图谱。
    """
    from src import config
    config.validate()
    _setup_logging(verbose)
    from src.db import database as db
    from src.agents import decomposer

    db.init_db()

    user_domains = [d.strip() for d in domains.split(",")] if domains else []

    console.rule(f"[bold blue]新建学习目标[/bold blue]")
    console.print(f"目标：[bold]{title}[/bold]")
    if user_domains:
        console.print(f"已知领域：{', '.join(user_domains)}")
    console.print()

    goal = db.create_goal(title=title, user_id=user_id)
    console.print(f"[dim]Goal ID: {goal['id']}[/dim]")
    console.rule("[yellow]开始拆解[/yellow]")

    try:
        atomic_nodes = decomposer.decompose_goal(
            goal_id=goal["id"],
            root_title=title,
            user_domains=user_domains,
            progress_cb=lambda msg: console.print(msg),
        )
    except Exception as e:
        console.print(f"[red]拆解失败：{e}[/red]")
        raise typer.Exit(1)

    console.rule("[green]拆解结果[/green]")

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("序号", style="dim", width=4)
    table.add_column("知识点", min_width=20)
    table.add_column("领域", width=12)
    table.add_column("难度", width=4)
    table.add_column("时间", width=6)
    table.add_column("严格度", width=8)

    for i, node in enumerate(atomic_nodes, 1):
        strictness_color = {
            "critical": "red",
            "standard": "yellow",
            "familiarity": "green",
        }.get(node.get("strictness_level", "standard"), "white")
        table.add_row(
            str(i),
            node["title"],
            node.get("domain", "—"),
            "★" * node.get("difficulty", 3),
            f"{node.get('est_minutes', '?')} min",
            f"[{strictness_color}]{node.get('strictness_level', 'standard')}[/{strictness_color}]",
        )

    console.print(table)
    console.print(
        f"\n[green]✓[/green] 共 {len(atomic_nodes)} 个原子知识点，"
        f"预计总学习时间 {sum(n.get('est_minutes', 0) for n in atomic_nodes)} 分钟"
    )
    console.print(f"\n使用 [bold]learn goal tree {goal['id'][:8]}[/bold] 查看完整知识图谱")


@goal_app.command("list")
def goal_list(user_id: str = typer.Option("default", "--user", "-u")):
    """列出所有学习目标。"""
    from src.db import database as db
    goals = db.list_goals(user_id=user_id)

    if not goals:
        rprint("[yellow]暂无学习目标。使用 learn goal new '...' 创建一个。[/yellow]")
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


@goal_app.command("tree")
def goal_tree(
    goal_id_prefix: str = typer.Argument(..., help="Goal ID 或前8位"),
    user_id: str = typer.Option("default", "--user", "-u"),
):
    """以树状图展示目标的知识图谱结构。"""
    from src.db import database as db
    from src.graph import dag

    goal = _resolve_goal(goal_id_prefix)
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
    from src.db import database as db
    from src.graph import dag

    goal = _resolve_goal(goal_id_prefix)
    if not goal:
        return

    nodes = dag.topological_order(goal["id"])
    if not nodes:
        rprint("[yellow]暂无知识点，请先运行 learn goal new 创建目标。[/yellow]")
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
    from src.db import database as db

    console.rule("[bold blue]今日学习状态[/bold blue]")

    # Due reviews
    due = db.get_due_reviews(user_id=user_id)
    if due:
        console.print(f"\n[red]🔔 待复习：{len(due)} 个[/red]")
        for r in due[:5]:
            console.print(f"   • {r['node_title']}  [{r.get('strictness_level','standard')}]")
        if len(due) > 5:
            console.print(f"   ... 还有 {len(due)-5} 个")
    else:
        console.print("\n[green]✓ 今日无待复习任务[/green]")

    # Goals summary
    goals = db.list_goals(user_id=user_id)
    if goals:
        console.print(f"\n📚 共 {len(goals)} 个学习目标")
        for g in goals:
            nodes = db.list_nodes_for_goal(g["id"], atomic_only=True)
            states = db.list_states(user_id=user_id)
            node_ids = {n["id"] for n in nodes}
            mastered = sum(
                1 for s in states
                if s["node_id"] in node_ids and s.get("status") == "mastered"
            )
            console.print(
                f"   [{g['status']}] {g['title'][:40]}  "
                f"{mastered}/{len(nodes)} 节点已掌握"
            )
    else:
        console.print("\n[yellow]尚无学习目标。使用 learn goal new '...' 开始。[/yellow]")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _resolve_goal(goal_id_prefix: str) -> Optional[dict]:
    """Find a goal by full ID or 8-char prefix."""
    from src.db import database as db
    goals = db.list_goals()
    matches = [g for g in goals if g["id"].startswith(goal_id_prefix)]
    if not matches:
        rprint(f"[red]找不到 Goal ID 以 '{goal_id_prefix}' 开头的目标[/red]")
        return None
    if len(matches) > 1:
        rprint(f"[red]找到多个匹配目标，请提供更长的 ID[/red]")
        return None
    return matches[0]


if __name__ == "__main__":
    app()
