"""
File: agents/assessor.py

Purpose:
    Initial knowledge assessment for a new learning goal.
    Probes the user with a small number of adaptive QA questions to determine
    which nodes they already know, so the learning path can skip mastered content.

Responsibilities:
    - Adaptively select the next probe node based on self-report level and
      prior answers (binary-search style across depth levels)
    - Generate one focused assessment question per probe node
    - Propagate mastery upward (prerequisites) and unknown downward (dependents)
    - Run an interactive CLI assessment loop with early-exit conditions

What this file does NOT do:
    - Teaching / Socratic dialogue (that lives in agents/teacher.py)
    - Full exam generation (that lives in agents/examiner.py)
    - Ebbinghaus scheduling (that lives in graph/dag.py)
    - Any DB schema changes

Key Design Decisions:
    - Self-report level (1-5) determines the starting depth for probing:
        1-2 → start shallow (beginners); 4-5 → start deep (experts)
    - Early exit: 3 consecutive passes → stop, mark rest mastered;
                  3 consecutive fails → stop, mark rest unknown
    - MAX_PROBES caps total questions regardless of graph size
    - Propagation is transitive: mastery propagates to ALL transitive prerequisites,
      unknown propagates to ALL transitive dependents
    - inferred_score for propagated mastery = mastery_threshold of each node
      (conservative: "inferred just-passing", not the actual probe score)

Inputs:
    - goal_id: the learning goal being assessed
    - user_id: learner identifier
    - self_report: integer 1-5 from user's self-assessment

Outputs:
    - Batch-upserted user_knowledge_state rows
    - Summary dict: {mastered, unknown, probes_done, total_nodes}
"""

from typing import Optional

from src.agents import client as llm
from src.db import database as db
from src.logger import get_logger

log = get_logger(__name__)

MAX_PROBES = 10  # maximum number of probe questions regardless of graph size

# ── System Prompt ──────────────────────────────────────────────────────────────

PROBE_QUESTION_SYSTEM = """\
你是一名知识评估专家。你的任务是为一个知识点生成一道简短的评估题。

要求：
- 题目简洁，能直接判断用户是否了解该概念（不需要很深入，判断"是否接触过"即可）
- 题型：简答题（不需要选项）
- expected_answer 要覆盖核心要点，但不需要太严格

输出 JSON：
{
  "question": "...",
  "expected_answer": "..."
}
""".strip()


# ── Question Generation ────────────────────────────────────────────────────────

def generate_probe_question(node: dict, model: Optional[str] = None) -> dict:
    """
    Generate a single assessment question for a probe node.

    Returns dict with: question, expected_answer.
    Raises ValueError if the LLM returns an unexpected format.
    Raises RuntimeError (from llm.call_json) on API failure — let callers decide.
    """
    user_prompt = (
        f"知识点：**{node['title']}**\n"
        f"描述：{node.get('description', '')[:300]}\n\n"
        "请生成一道简短的评估题，判断用户是否了解这个概念。"
    )
    result = llm.call_json(PROBE_QUESTION_SYSTEM, user_prompt, max_tokens=512, model=model)
    if not isinstance(result, dict):
        raise ValueError(f"generate_probe_question returned unexpected type: {type(result)}")
    return {
        "question": result.get("question", ""),
        "expected_answer": result.get("expected_answer", ""),
    }


# ── Probe Node Selection ───────────────────────────────────────────────────────

def next_probe_node(
    goal_id: str,
    history: list[dict],
    self_report: int,
) -> Optional[dict]:
    """
    Dynamically select the next probe node.

    history items: {node_id, depth_level, score, passed}
    self_report: 1-5

    Returns None when assessment should stop:
        - 3 consecutive failures (score < 0.3)
        - 3 consecutive passes
        - All nodes already probed
        - MAX_PROBES reached
    """
    all_nodes = db.list_nodes_for_goal(goal_id, atomic_only=True)
    if not all_nodes:
        return None

    # Hard cap
    if len(history) >= MAX_PROBES:
        return None

    probed_ids = {h["node_id"] for h in history}

    # Early exit: 3 consecutive fails (score < 0.3)
    if len(history) >= 3:
        last3 = history[-3:]
        if all(h["score"] < 0.3 for h in last3):
            log.info("Assessment: 3 consecutive failures — stopping early")
            return None
        if all(h["passed"] for h in last3):
            log.info("Assessment: 3 consecutive passes — stopping early")
            return None

    # Group by depth level
    depth_groups: dict[int, list[dict]] = {}
    for n in all_nodes:
        d = n.get("depth_level", 0)
        depth_groups.setdefault(d, []).append(n)

    depths = sorted(depth_groups.keys())
    if not depths:
        return None

    min_depth = depths[0]
    max_depth = depths[-1]

    # Determine target depth
    if not history:
        # First probe: use self-report to pick starting depth
        if self_report <= 2:
            target_depth = min_depth
        elif self_report >= 4:
            target_depth = max_depth
        else:
            target_depth = depths[len(depths) // 2]
    else:
        last = history[-1]
        last_depth = last["depth_level"]
        if last["passed"]:
            # Go deeper
            deeper = [d for d in depths if d > last_depth]
            target_depth = deeper[0] if deeper else max_depth
        else:
            # Go shallower
            shallower = [d for d in depths if d < last_depth]
            target_depth = shallower[-1] if shallower else min_depth

    # Find an unprobed candidate at target_depth, then nearest depths
    search_order = sorted(depths, key=lambda d: (abs(d - target_depth), d))
    for d in search_order:
        candidates = [n for n in depth_groups.get(d, []) if n["id"] not in probed_ids]
        if not candidates:
            continue
        # Prefer standard/critical over familiarity
        candidates.sort(key=lambda n: (n.get("strictness_level") == "familiarity", n["title"]))
        return candidates[0]

    return None  # all probed


# ── Mastery Propagation ────────────────────────────────────────────────────────

def _propagate_mastery(
    node_id: str,
    user_id: str = "default",
    inferred_score: float = 0.85,
    _visited: Optional[set] = None,
):
    """
    Mark node_id as mastered and recursively mark all transitive prerequisites.

    Uses inferred_score as the raw_score for the node itself.
    For prerequisites, uses each node's mastery_threshold as raw_score
    (conservative: "inferred just-passing").
    """
    if _visited is None:
        _visited = set()
    if node_id in _visited:
        return
    _visited.add(node_id)

    node = db.get_node(node_id)
    if not node:
        return

    threshold = node.get("mastery_threshold", 0.80)
    db.upsert_state(
        node_id=node_id,
        status="mastered",
        raw_score=inferred_score,
        stability=1.0,
        user_id=user_id,
    )
    log.debug("Propagate mastery → %s (%s)", node["title"], node_id[:8])

    # Recurse into prerequisites
    prereqs = db.get_prerequisites(node_id)
    for prereq in prereqs:
        _propagate_mastery(
            prereq["id"],
            user_id=user_id,
            inferred_score=prereq.get("mastery_threshold", 0.80),
            _visited=_visited,
        )


def _propagate_unknown(
    node_id: str,
    user_id: str = "default",
    _visited: Optional[set] = None,
):
    """
    Mark node_id as unknown and recursively mark all transitive dependents.

    Nodes already in 'mastered' state are NOT downgraded — an explicit probe
    pass takes precedence over inferred unknown propagation.
    """
    if _visited is None:
        _visited = set()
    if node_id in _visited:
        return
    _visited.add(node_id)

    node = db.get_node(node_id)
    if not node:
        return

    # Don't overwrite an explicitly mastered state
    existing = db.get_state(node_id, user_id)
    if existing and existing.get("status") == "mastered":
        log.debug("Propagate unknown skipped (already mastered) → %s", node["title"])
        return

    db.upsert_state(
        node_id=node_id,
        status="unknown",
        raw_score=0.0,
        stability=1.0,
        user_id=user_id,
    )
    log.debug("Propagate unknown → %s (%s)", node["title"], node_id[:8])

    # Recurse into dependents
    dependents = db.get_dependents(node_id)
    for dep in dependents:
        _propagate_unknown(dep["id"], user_id=user_id, _visited=_visited)


# ── Interactive Assessment Loop ────────────────────────────────────────────────

def run_assessment_loop(
    goal_id: str,
    user_id: str = "default",
    model: Optional[str] = None,
    console=None,
) -> dict:
    """
    Run the interactive initial assessment for a learning goal.

    Flow:
        1. Ask self-report level (1-5, no API call)
        2. Adaptively probe nodes via generate_probe_question + score_answer
        3. Propagate mastery / unknown based on each answer
        4. Stop on early-exit conditions or MAX_PROBES
        5. Return summary: {mastered, unknown, probes_done, total_nodes}
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from src.agents.examiner import score_answer

    if console is None:
        console = Console()

    all_nodes = db.list_nodes_for_goal(goal_id, atomic_only=True)
    total_nodes = len(all_nodes)

    if total_nodes == 0:
        console.print("[yellow]该目标暂无知识点，请先完成目标拆解。[/yellow]")
        return {"mastered": 0, "unknown": 0, "probes_done": 0, "total_nodes": 0}

    goal = db.get_goal(goal_id)
    goal_title = goal["title"] if goal else goal_id

    console.rule(f"[bold cyan]🧭 初始评估：{goal_title}[/bold cyan]")
    console.print(
        "\n这将帮助系统了解你的已知范围，跳过你已掌握的内容。\n"
        "[dim]每道题只需简短回答，系统会自动判断。输入 /exit 随时终止（已答结果会保留）。[/dim]\n"
    )

    # ── Step 1: Self-report ────────────────────────────────────────────────────
    console.print("[bold]你对这个领域的整体了解程度？[/bold]")
    console.print("  [1] 完全陌生，第一次接触")
    console.print("  [2] 有一点了解，但没系统学过")
    console.print("  [3] 学过一部分，有些地方不清楚")
    console.print("  [4] 用过，比较熟悉")
    console.print("  [5] 已经深度掌握\n")

    try:
        raw = input("请输入数字 (1-5)：").strip()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]评估已取消。[/yellow]")
        return {"mastered": 0, "unknown": 0, "probes_done": 0, "total_nodes": total_nodes}

    try:
        self_report = max(1, min(5, int(raw)))
    except ValueError:
        self_report = 3
        console.print("[dim]无效输入，默认设为 3（学过一部分）。[/dim]")

    console.print()

    # ── Step 2: Adaptive probe loop ────────────────────────────────────────────
    history: list[dict] = []
    threshold_default = 0.80

    while True:
        probe_node = next_probe_node(goal_id, history=history, self_report=self_report)
        if probe_node is None:
            break

        # Generate question
        try:
            qa = generate_probe_question(probe_node, model=model)
        except Exception as e:
            log.warning("Failed to generate question for '%s': %s", probe_node["title"], e)
            # Skip this node (add to history as skipped with neutral score)
            history.append({
                "node_id": probe_node["id"],
                "depth_level": probe_node.get("depth_level", 0),
                "score": 0.5,
                "passed": False,
            })
            continue

        q_num = len(history) + 1
        node_threshold = probe_node.get("mastery_threshold", threshold_default)

        console.print(Panel(
            f"[bold]第 {q_num} 题[/bold]  [dim]{probe_node['title']}[/dim]\n\n"
            + qa["question"],
            border_style="cyan",
        ))

        try:
            user_answer = input("你的回答：").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]评估中断，已保留现有结果。[/yellow]")
            break

        if user_answer.lower() in ("/exit", "/quit"):
            console.print("[yellow]评估提前结束，已保留现有结果。[/yellow]")
            break

        if not user_answer:
            user_answer = "（未作答）"

        # Score
        console.print("[dim]评分中...[/dim]")
        scoring = score_answer(
            question=qa["question"],
            expected_answer=qa["expected_answer"],
            user_answer=user_answer,
            strictness=probe_node.get("strictness_level", "standard"),
            model=model,
        )
        score_val = scoring["score"]
        passed = score_val >= node_threshold

        score_color = "green" if score_val >= 0.8 else ("yellow" if score_val >= 0.5 else "red")
        console.print(f"[{score_color}]得分：{score_val:.0%}[/{score_color}]  ", end="")
        if passed:
            console.print("[green]✓ 已掌握[/green]")
            _propagate_mastery(probe_node["id"], user_id=user_id, inferred_score=score_val)
        elif score_val >= 0.5:
            console.print("[yellow]部分了解[/yellow]")
            # Partial: only mark current node as learning, no propagation
            db.upsert_state(
                node_id=probe_node["id"],
                status="learning",
                raw_score=score_val,
                stability=1.0,
                user_id=user_id,
            )
        else:
            console.print("[red]✗ 尚未掌握[/red]")
            _propagate_unknown(probe_node["id"], user_id=user_id)

        console.print()

        history.append({
            "node_id": probe_node["id"],
            "depth_level": probe_node.get("depth_level", 0),
            "score": score_val,
            "passed": passed,
        })

    # ── Step 3: Mark remaining nodes as unknown (if not yet touched) ──────────
    all_node_ids = {n["id"] for n in all_nodes}
    existing_states = {s["node_id"] for s in db.list_states(user_id=user_id)}
    untouched = all_node_ids - existing_states
    for nid in untouched:
        db.upsert_state(
            node_id=nid,
            status="unknown",
            raw_score=0.0,
            stability=1.0,
            user_id=user_id,
        )

    # ── Step 4: Build summary ──────────────────────────────────────────────────
    final_states = db.list_states(user_id=user_id)
    state_map = {s["node_id"]: s for s in final_states if s["node_id"] in all_node_ids}

    mastered_count = sum(1 for s in state_map.values() if s["status"] == "mastered")
    unknown_count  = sum(1 for s in state_map.values() if s["status"] != "mastered")

    _print_assessment_summary(console, mastered_count, unknown_count,
                              len(history), total_nodes, state_map, all_nodes)

    return {
        "mastered": mastered_count,
        "unknown": unknown_count,
        "probes_done": len(history),
        "total_nodes": total_nodes,
    }


def _print_assessment_summary(
    console,
    mastered: int,
    unknown: int,
    probes_done: int,
    total_nodes: int,
    state_map: dict,
    all_nodes: list[dict],
):
    from rich.table import Table

    console.rule("[bold cyan]评估结果[/bold cyan]")
    console.print(
        f"\n共 {total_nodes} 个知识点，完成 {probes_done} 道探测题\n"
        f"[green]✓ 已掌握：{mastered} 个[/green]   "
        f"[yellow]待学习：{unknown} 个[/yellow]\n"
    )

    if mastered > 0:
        console.print(f"[dim]系统已跳过 {mastered} 个已掌握节点，将从最优位置开始学习。[/dim]")

    # Find a recommended starting node (shallowest unknown)
    unknown_nodes = [
        n for n in all_nodes
        if state_map.get(n["id"], {}).get("status", "unknown") != "mastered"
    ]
    if unknown_nodes:
        unknown_nodes.sort(key=lambda n: n.get("depth_level", 0))
        first = unknown_nodes[0]
        console.print(
            f"\n[bold cyan]建议从「{first['title']}」开始学习[/bold cyan]"
            f"  [dim]({first['id'][:8]})[/dim]"
        )
        console.print(
            f"[dim]运行：python main.py learn start {first['id'][:8]}[/dim]\n"
        )
