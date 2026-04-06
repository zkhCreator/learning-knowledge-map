"""
File: agents/decomposer.py

Purpose:
    Recursive goal decomposition using a Forward Agent (plan) and
    a Reverse Agent (review), mirroring the plan/subagent quality-gate
    pattern described in DESIGN.md.

Responsibilities:
    - Forward Agent: decompose a goal/node into atomic child nodes
    - Reverse Agent: review the decomposition for completeness,
      correctness, and appropriate granularity
    - Orchestrate the forward → reverse → retry loop
    - Persist all nodes and edges to the database
    - Recursively decompose non-atomic children until all leaves satisfy
      the atom condition

What this file does NOT do:
    - Teaching or QA (that's in agents/teacher.py)
    - Path-finding or scheduling (that's in graph/)
    - CLI output (that's in cli/main.py)

Key Design Decisions:
    - Forward and Reverse Agents are separate prompt calls (not one conversation).
      This avoids confirmation bias where the reviewer is anchored to the plan.
    - Decomposition result is always JSON; _extract_json handles fences robustly.
    - Max retries per level: config.MAX_DECOMPOSE_RETRIES (default 2).
    - Max depth: config.MAX_DECOMPOSE_DEPTH (default 6).

Inputs:
    - goal_id: the learning_goals row this decomposition belongs to
    - title: the text of the node to decompose
    - user_profile: optional dict with user's existing knowledge domains

Outputs:
    - List of created knowledge_node dicts (leaf nodes only)
    - Side effect: all nodes + edges written to DB
"""

import json
from typing import Callable, Optional

from src import config
from src.agents import client as llm
from src.db import database as db
from src.logger import get_logger

log = get_logger(__name__)

# ── System prompts ─────────────────────────────────────────────────────────────

FORWARD_SYSTEM = """\
你是一个学习系统的知识拆解专家。你的任务是将一个学习目标拆解为结构清晰的子知识点。

输出规则：
- 必须以 JSON 格式输出，不要输出其他内容
- 每个子节点包含：title, description, domain, concept_fingerprint, difficulty(1-5),
  est_minutes, prerequisites(子节点之间的依赖，使用 title 引用), strictness_level,
  risk_note, qa_draft(3个验证问题)
- concept_fingerprint 是 2-3 个抽象模式标签，用于跨领域类比，例如 ["隔离性","状态一致性"]
- strictness_level 必须是 "critical" / "standard" / "familiarity" 之一
- is_atomic 表示该节点是否是原子节点（不需要进一步拆解）

原子节点的判定标准（同时满足）：
1. 能生成至少 3 个有区分度的 QA 验证问题
2. est_minutes <= {atom_max_minutes}
3. 不包含需要独立学习的子概念

输出示例：
{{
  "children": [
    {{
      "title": "容器基础概念",
      "description": "理解容器 vs 虚拟机的区别，namespace 和 cgroup 的作用",
      "domain": "容器技术",
      "concept_fingerprint": ["隔离性", "资源限制", "进程管理"],
      "difficulty": 2,
      "est_minutes": 10,
      "prerequisites": [],
      "strictness_level": "standard",
      "risk_note": "",
      "is_atomic": true,
      "qa_draft": [
        "容器和虚拟机的核心区别是什么？",
        "Linux namespace 提供了哪些隔离维度？",
        "cgroup 的主要作用是什么？"
      ]
    }}
  ]
}}
""".strip()

REVERSE_SYSTEM = """\
你是一个学习系统的质量审核专家。你的任务是 review 一份知识拆解方案，找出问题并给出明确结论。

审核维度（逐条检查）：
1. 完备性：是否覆盖了目标所需的所有知识？有无重要遗漏？
2. 粒度合适性：每个节点的 est_minutes 是否合理？有无需要进一步拆分或合并的节点？
3. 依赖正确性：prerequisites 的顺序和关系是否正确？是否存在循环依赖？
4. 跨域前置：是否遗漏了其他领域的前置知识？
5. 严格度标注：strictness_level 的判定是否合理？

输出规则：
- 必须以 JSON 格式输出，不要输出其他内容
- approved: true/false
- issues: 问题列表（approved=true 时可以为空）
- suggestions: 针对 Forward Agent 的具体修改建议

输出示例：
{{
  "approved": false,
  "issues": [
    "缺少 Linux 网络基础作为前置知识",
    "'K8s 架构概览' 的 est_minutes=5 太短，实际需要 15 分钟"
  ],
  "suggestions": "请在子节点列表中增加 'Linux 网络基础' 节点，并调整 K8s 架构概览的 est_minutes 到 15。"
}}
""".strip()


# ── Forward Agent ──────────────────────────────────────────────────────────────

def forward_decompose(
    target_title: str,
    target_description: str,
    parent_context: str,
    user_domains: list[str],
    depth: int,
    feedback: str = "",
) -> dict:
    """
    Ask the forward agent to decompose `target_title` into child nodes.
    Returns the parsed JSON dict with a "children" key.
    """
    system = FORWARD_SYSTEM.format(atom_max_minutes=config.ATOM_MAX_MINUTES)

    user_parts = [
        f"## 当前目标\n**{target_title}**",
    ]
    if target_description:
        user_parts.append(f"描述：{target_description}")
    if parent_context:
        user_parts.append(f"上层目标：{parent_context}")
    if user_domains:
        user_parts.append(f"用户已知领域（可用于类比）：{', '.join(user_domains)}")
    user_parts.append(f"当前递归深度：{depth}（最大深度 {config.MAX_DECOMPOSE_DEPTH}）")
    if depth >= config.MAX_DECOMPOSE_DEPTH - 1:
        user_parts.append("⚠️ 接近最大深度，请尽量将所有子节点标记为 is_atomic=true。")
    if feedback:
        user_parts.append(f"\n## Reviewer 的修改意见\n{feedback}\n请根据以上意见修改拆解方案。")

    user_prompt = "\n\n".join(user_parts)
    result = llm.call_json(system, user_prompt, max_tokens=4096)
    # Normalize: some models return the list directly
    if isinstance(result, list):
        return {"children": result}
    return result


# ── Reverse Agent ──────────────────────────────────────────────────────────────

def reverse_review(
    target_title: str,
    children: list[dict],
    user_domains: list[str],
) -> dict:
    """
    Ask the reverse agent to review the decomposition plan.
    Returns {"approved": bool, "issues": [...], "suggestions": str}
    """
    user_prompt = (
        f"## 被拆解的目标\n{target_title}\n\n"
        f"## 正向 Agent 提交的拆解方案\n"
        f"```json\n{json.dumps(children, ensure_ascii=False, indent=2)}\n```\n\n"
        f"用户已知领域：{', '.join(user_domains) or '未知'}"
    )
    result = llm.call_json(REVERSE_SYSTEM, user_prompt, max_tokens=2048)
    if not isinstance(result, dict):
        # Fallback: treat any non-dict as approval
        return {"approved": True, "issues": [], "suggestions": ""}
    return result


# ── Orchestrator ───────────────────────────────────────────────────────────────

def decompose_goal(
    goal_id: str,
    root_title: str,
    root_description: str = "",
    user_domains: list[str] | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> list[dict]:
    """
    Entry point: recursively decompose root_title and persist all nodes/edges.

    Args:
        goal_id:        The learning_goals row this belongs to.
        root_title:     Top-level goal text (e.g. "学会 Kubernetes 集群管理").
        root_description: Optional description.
        user_domains:   List of domains the user already knows.
        progress_cb:    Optional callback for progress messages.

    Returns:
        List of atomic (leaf) knowledge_node dicts.
    """
    user_domains = user_domains or []
    log = progress_cb or (lambda msg: print(msg))

    # Create the root node (non-atomic placeholder)
    root_node = db.create_node(
        title=root_title,
        goal_id=goal_id,
        description=root_description,
        depth_level=0,
        is_atomic=False,
    )
    # Link goal → root node
    db.update_goal(goal_id, root_node=root_node["id"], status="active")

    log(f"🌱 开始拆解：{root_title}")

    atomic_nodes: list[dict] = []

    _recurse(
        parent_node=root_node,
        parent_title=root_title,
        parent_description=root_description,
        parent_context="",
        goal_id=goal_id,
        user_domains=user_domains,
        depth=1,
        atomic_nodes=atomic_nodes,
        log=log,
    )

    log(f"\n✅ 拆解完成，共生成 {len(atomic_nodes)} 个原子知识点")
    return atomic_nodes


def _recurse(
    parent_node: dict,
    parent_title: str,
    parent_description: str,
    parent_context: str,
    goal_id: str,
    user_domains: list[str],
    depth: int,
    atomic_nodes: list[dict],
    log: Callable[[str], None],
):
    """Recursive helper that decomposes a single node and persists children."""
    indent = "  " * depth
    log(f"{indent}📋 正在拆解：{parent_title}")

    feedback = ""
    children_data: list[dict] = []

    for attempt in range(config.MAX_DECOMPOSE_RETRIES + 1):
        # ── Forward Agent ──────────────────────────────────────────────────
        log.info(
            "[depth=%d] Forward Agent: decomposing '%s' (attempt %d/%d)",
            depth, parent_title, attempt + 1, config.MAX_DECOMPOSE_RETRIES + 1,
        )
        try:
            forward_result = forward_decompose(
                target_title=parent_title,
                target_description=parent_description,
                parent_context=parent_context,
                user_domains=user_domains,
                depth=depth,
                feedback=feedback,
            )
        except Exception as e:
            log.error("[depth=%d] Forward Agent failed: %s", depth, e)
            log(f"{indent}⚠️  正向 Agent 调用失败: {e}")
            break

        children_data = forward_result.get("children", [])
        if not children_data:
            log.warning("[depth=%d] Forward Agent returned empty children list", depth)
            log(f"{indent}⚠️  正向 Agent 返回空列表，跳过")
            break

        log.info(
            "[depth=%d] Forward Agent returned %d children: %s",
            depth,
            len(children_data),
            [c.get("title") for c in children_data],
        )
        log.debug(
            "[depth=%d] Forward Agent full output:\n%s",
            depth,
            json.dumps(children_data, ensure_ascii=False, indent=2),
        )
        log(f"{indent}   正向 Agent 返回 {len(children_data)} 个子节点")

        # Force-atomic if at max depth
        if depth >= config.MAX_DECOMPOSE_DEPTH:
            log.warning("[depth=%d] Max depth reached — forcing all nodes to atomic", depth)
            for c in children_data:
                c["is_atomic"] = True

        # ── Reverse Agent ──────────────────────────────────────────────────
        log.info("[depth=%d] Reverse Agent: reviewing plan for '%s'", depth, parent_title)
        try:
            review = reverse_review(parent_title, children_data, user_domains)
        except Exception as e:
            log.error("[depth=%d] Reverse Agent failed: %s — accepting forward result", depth, e)
            log(f"{indent}⚠️  反向 Agent 调用失败: {e}，直接采用正向结果")
            review = {"approved": True, "issues": [], "suggestions": ""}

        log.debug(
            "[depth=%d] Reverse Agent decision: approved=%s  issues=%s",
            depth,
            review.get("approved"),
            review.get("issues"),
        )

        if review.get("approved", True):
            log.info("[depth=%d] Reverse Agent approved the plan ✓", depth)
            log(f"{indent}   ✓ 反向 Agent 审核通过")
            break
        else:
            issues = review.get("issues", [])
            feedback = review.get("suggestions", "")
            log.warning(
                "[depth=%d] Reverse Agent rejected — %d issues: %s",
                depth, len(issues), issues,
            )
            log(f"{indent}   ✗ 反向 Agent 发现 {len(issues)} 个问题，重新拆解...")
            for issue in issues[:3]:
                log(f"{indent}     • {issue}")
            if attempt == config.MAX_DECOMPOSE_RETRIES:
                log.warning("[depth=%d] Max retries reached — using last forward result", depth)
                log(f"{indent}   ⚡ 达到最大重试次数，采用当前方案")

    if not children_data:
        return

    # ── Persist children + edges ───────────────────────────────────────────
    # Build a title→node_id map for prerequisite edges
    title_to_node: dict[str, dict] = {}

    for child_data in children_data:
        qa_set = [
            {"question": q, "expected_answer": "", "difficulty": 3}
            for q in child_data.get("qa_draft", [])
        ]
        child_node = db.create_node(
            title=child_data.get("title", "未命名"),
            goal_id=goal_id,
            description=child_data.get("description", ""),
            domain=child_data.get("domain", ""),
            concept_fingerprint=child_data.get("concept_fingerprint", []),
            difficulty=child_data.get("difficulty", 3),
            est_minutes=child_data.get("est_minutes", 10),
            qa_set=qa_set,
            depth_level=depth,
            parent_node=parent_node["id"],
            strictness_level=child_data.get("strictness_level", "standard"),
            risk_note=child_data.get("risk_note", ""),
            is_atomic=bool(child_data.get("is_atomic", True)),
        )
        title_to_node[child_data.get("title", "")] = child_node

        # Edge: parent → child (parent must be learned before child)
        db.create_edge(
            from_node=parent_node["id"],
            to_node=child_node["id"],
            edge_type="prerequisite",
        )

    # Intra-sibling prerequisite edges (based on prerequisites field)
    for child_data in children_data:
        child_node = title_to_node.get(child_data.get("title", ""))
        if not child_node:
            continue
        for prereq_title in child_data.get("prerequisites", []):
            prereq_node = title_to_node.get(prereq_title)
            if prereq_node:
                db.create_edge(
                    from_node=prereq_node["id"],
                    to_node=child_node["id"],
                    edge_type="prerequisite",
                )

    # ── Recurse into non-atomic children ──────────────────────────────────
    for child_data in children_data:
        child_node = title_to_node.get(child_data.get("title", ""))
        if not child_node:
            continue

        if child_data.get("is_atomic", True):
            log(f"{indent}   🔵 原子节点：{child_node['title']} ({child_data.get('est_minutes', 10)} min)")
            atomic_nodes.append(child_node)
        else:
            _recurse(
                parent_node=child_node,
                parent_title=child_node["title"],
                parent_description=child_node.get("description", ""),
                parent_context=parent_title,
                goal_id=goal_id,
                user_domains=user_domains,
                depth=depth + 1,
                atomic_nodes=atomic_nodes,
                log=log,
            )
