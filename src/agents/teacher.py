"""
File: agents/teacher.py

Purpose:
    Outline generation and Socratic dialogue for individual knowledge nodes.

Responsibilities:
    - Forward Agent: generate structured learning outline (3-8 sections) for a node
    - Reverse Agent: validate outline accuracy and completeness
    - WebSearch: on-demand enrichment for sections marked needs_search=true
    - Socratic dialogue: multi-turn conversation anchored to the outline, with
      per-turn coverage tracking (which outline sections have been discussed)
    - Persist outline, session, and chat history to the database

What this file does NOT do:
    - Exam logic (that lives in agents/examiner.py)
    - CLI output / Rich formatting (that lives in cli/main.py)
    - Ebbinghaus scheduling (that lives in graph/dag.py)

Key Design Decisions:
    - Coverage tracking is embedded in the Socratic agent's JSON response
      (fields: "response" + "newly_covered_sections") to avoid an extra API call.
    - WebSearch is on-demand only: called per section only when needs_search=true
      AND a SEARCH_API_KEY is configured. Degrades gracefully otherwise.
    - The chat loop runs as a blocking REPL inside the CLI process; it does not
      use async or streaming.

Inputs:
    - node_id: knowledge_nodes row to teach
    - user_id: learner identifier (default "default")
    - model: optional model override

Outputs:
    - Outline persisted to node_outlines table
    - Session + chat history persisted to learning_sessions / chat_messages
    - Progress float (0.0–1.0) returned after each chat turn
"""

import json
from typing import Callable, Optional

from src import config
from src.agents import client as llm
from src.agents.mnemonic import build_mnemonic_prompt_snippet, get_dominant_strategy
from src.db import database as db
from src.logger import get_logger

log = get_logger(__name__)


# ── System Prompts ─────────────────────────────────────────────────────────────

OUTLINE_FORWARD_SYSTEM = """\
你是一名专业的知识大纲设计师。你的任务是为一个原子知识点生成结构化的学习大纲。

输出规则：
- 必须以 JSON 格式输出，不要输出任何其他内容
- 大纲分为 3-8 个小节（sections），每节聚焦一个具体的子主题
- 每个 section 包含：index(从1开始), title, content(150-300字的讲解), needs_search, sources(空列表), analogy, analogy_source_node
- needs_search 判断标准：
  - true：近 1-2 年的新知识、Agent 对内容不确信、涉及最新论文/标准/数据
  - false：经典基础知识、稳定的学科概念
- analogy：如果能联系用户已知领域提供类比，填写一句话类比说明；否则为 null
- analogy_source_node：类比来源的领域/知识点名称，没有类比时为 null

输出格式：
{
  "sections": [
    {
      "index": 1,
      "title": "...",
      "content": "...",
      "needs_search": false,
      "sources": [],
      "analogy": "类似于...",
      "analogy_source_node": "...",
      "covered": false
    }
  ]
}
""".strip()

OUTLINE_REVERSE_SYSTEM = """\
你是一名专业的知识审核专家。你的任务是审核一份学习大纲，确保知识准确、结构合理。

审核维度：
1. 知识准确性：内容是否正确？有无明显的知识性错误或过时信息？
2. 结构合理性：章节划分是否清晰？顺序是否符合认知规律（基础 → 应用）？
3. 覆盖完整性：是否覆盖了该知识点的核心内容？有无重要遗漏？
4. 类比恰当性：如果有类比，是否准确且有助于理解？

输出规则：
- 必须以 JSON 格式输出
- approved: true/false
- issues: 问题列表（approved=true 时可以为空）
- corrections: 需要修正的具体内容（格式：{section_index: 修正意见}）

{
  "approved": true,
  "issues": [],
  "corrections": {}
}
""".strip()

SOCRATIC_SYSTEM = """\
你是一名苏格拉底式教学助手。你正在帮助学生学习以下知识点：

## 知识点
**{node_title}**
{node_description}

## 学习大纲
{outline_text}

## 教学规则
1. 不要直接给出答案——通过提问引导学生思考，让他们自己得出结论
2. 每次回复聚焦大纲中的一个或相关的几个小节
3. 当学生回答正确时给予肯定，并自然地引入下一个概念
4. 当学生困惑时，先用类比（如果大纲中有）降低理解门槛
5. 回复长度适中（150-300字），保持对话节奏
6. 追踪已覆盖的大纲小节
7. 如果大纲中的小节包含助记信息（mnemonic），在引入新概念或学生困惑时自然地融入这些助记线索，帮助学生建立记忆锚点

## 输出格式（必须严格遵守）
必须以 JSON 格式输出，包含两个字段：
- "response"：你的对话回复内容（学生直接看到的文字）
- "newly_covered_sections"：本轮对话中新覆盖到的大纲小节序号列表（整数数组，仅包含本轮新增的，已覆盖过的不要重复）

示例：
{{"response": "很好！你理解了核心概念。那么，当系统崩溃时...", "newly_covered_sections": [2]}}
""".strip()


# ── Outline Generation ─────────────────────────────────────────────────────────

def generate_outline(
    node_id: str,
    user_id: str = "default",
    model: Optional[str] = None,
    user_domains: list[str] | None = None,
    force_regenerate: bool = False,
    progress_cb: Callable[[str], None] | None = None,
) -> dict:
    """
    Generate and validate a learning outline for the given node.

    Checks for an existing validated outline first (unless force_regenerate=True).
    Runs Forward Agent → optional WebSearch → Reverse Agent → persists to DB.

    Returns the outline dict with sections parsed from JSON.
    """
    progress = progress_cb or (lambda _: None)
    node = db.get_node(node_id)
    if not node:
        raise ValueError(f"Node not found: {node_id}")

    # Return existing outline if already validated
    if not force_regenerate:
        existing = db.get_outline(node_id, user_id)
        if existing and existing.get("status") in ("validated", "active", "completed"):
            log.info("Reusing existing outline for node %s (status=%s)", node_id, existing["status"])
            progress("📄 使用已有大纲")
            return existing

    progress(f"📝 正在为「{node['title']}」生成学习大纲...")

    # ── Determine mnemonic strategy (if user has a cognitive profile) ──────────
    mnemonic_strategy: Optional[str] = None
    mnemonic_snippet = ""
    profile = db.get_cognitive_profile(user_id)
    if profile and profile.get("assessed"):
        weights = {
            "spatial": profile["spatial_weight"],
            "symbolic": profile["symbolic_weight"],
            "narrative": profile["narrative_weight"],
        }
        mnemonic_strategy = get_dominant_strategy(weights)
        mnemonic_snippet = build_mnemonic_prompt_snippet(mnemonic_strategy)
        log.info("Mnemonic strategy for user '%s': %s", user_id, mnemonic_strategy)

    # Build system prompt (optionally augmented with mnemonic instructions)
    system_prompt = OUTLINE_FORWARD_SYSTEM
    if mnemonic_snippet:
        system_prompt = system_prompt + "\n\n" + mnemonic_snippet

    # Build user prompt for forward agent
    user_parts = [
        f"## 知识点\n**{node['title']}**",
    ]
    if node.get("description"):
        user_parts.append(f"描述：{node['description']}")
    if node.get("domain"):
        user_parts.append(f"所属领域：{node['domain']}")
    if user_domains:
        user_parts.append(f"学习者已知领域（请尽量提供类比）：{', '.join(user_domains)}")
    user_parts.append("请生成该知识点的结构化学习大纲（3-8节）。")

    user_prompt = "\n\n".join(user_parts)

    # ── Forward Agent ──────────────────────────────────────────────────────────
    log.info("Outline Forward Agent: generating for node '%s'", node["title"])
    result = llm.call_json(system_prompt, user_prompt, max_tokens=4096, model=model)

    sections: list[dict] = result.get("sections", []) if isinstance(result, dict) else []
    if not sections:
        raise ValueError("Forward Agent returned empty outline sections.")

    # Normalise section fields
    for i, sec in enumerate(sections):
        sec.setdefault("index", i + 1)
        sec.setdefault("sources", [])
        sec.setdefault("analogy", None)
        sec.setdefault("analogy_source_node", None)
        sec.setdefault("covered", False)
        sec.setdefault("needs_search", False)

    progress(f"   ✓ 正向 Agent 生成了 {len(sections)} 个章节")

    # ── WebSearch (on-demand) ──────────────────────────────────────────────────
    search_sections = [s for s in sections if s.get("needs_search")]
    if search_sections:
        if _search_available():
            progress(f"   🔍 对 {len(search_sections)} 个章节执行 WebSearch...")
            for sec in search_sections:
                query = f"{node['title']} {sec['title']}"
                results = _websearch(query)
                if results:
                    sec["sources"] = results
                    log.info("WebSearch for section '%s': %d results", sec["title"], len(results))
        else:
            log.info("WebSearch skipped (SEARCH_API_KEY not configured)")
            progress("   ⚠️  WebSearch 未配置（跳过），使用模型自身知识")

    # ── Reverse Agent ──────────────────────────────────────────────────────────
    progress("   🔍 反向 Agent 正在审核大纲准确性...")
    log.info("Outline Reverse Agent: reviewing for node '%s'", node["title"])

    review_prompt = (
        f"## 知识点\n{node['title']}\n\n"
        f"## 待审核大纲\n```json\n"
        f"{json.dumps(sections, ensure_ascii=False, indent=2)}\n```"
    )
    review = llm.call_json(OUTLINE_REVERSE_SYSTEM, review_prompt, max_tokens=2048, model=model)

    if isinstance(review, dict):
        if review.get("approved", True):
            progress("   ✓ 反向 Agent 审核通过")
            log.info("Outline Reverse Agent approved")
        else:
            issues = review.get("issues", [])
            corrections = review.get("corrections", {})
            log.warning("Outline Reverse Agent found issues: %s", issues)
            progress(f"   ⚠️  反向 Agent 发现 {len(issues)} 个问题（已记录，继续使用当前大纲）")
            # Apply inline corrections where possible
            for sec_idx_str, correction in corrections.items():
                try:
                    idx = int(sec_idx_str)
                    for sec in sections:
                        if sec.get("index") == idx:
                            sec["correction_note"] = correction
                except (ValueError, TypeError):
                    pass

    # ── Persist ────────────────────────────────────────────────────────────────
    outline = db.create_outline(node_id=node_id, sections=sections, user_id=user_id)
    db.update_outline(outline["id"], status="validated")
    outline["status"] = "validated"
    outline["sections"] = sections
    log.info("Outline persisted for node %s (outline_id=%s)", node_id, outline["id"])
    progress(f"   ✓ 大纲已保存（{len(sections)} 节）")

    # ── Persist mnemonic anchors (if mnemonic strategy was used) ──────────────
    if mnemonic_strategy:
        # Clear any old anchors for this node (e.g. from a previous regeneration)
        db.delete_mnemonic_anchors(node_id=node_id, user_id=user_id)
        anchor_count = 0
        for sec in sections:
            mnemonic_data = sec.get("mnemonic")
            if mnemonic_data and isinstance(mnemonic_data, dict) and mnemonic_data.get("content"):
                try:
                    db.create_mnemonic_anchor(
                        user_id=user_id,
                        node_id=node_id,
                        strategy=mnemonic_data.get("strategy", mnemonic_strategy),
                        section_index=sec.get("index"),
                        content=mnemonic_data["content"],
                        palace_location=mnemonic_data.get("palace_location"),
                    )
                    anchor_count += 1
                except Exception as e:
                    log.warning("Failed to save mnemonic anchor for section %s: %s", sec.get("index"), e)
        if anchor_count:
            log.info("Saved %d mnemonic anchors for node %s", anchor_count, node_id)
            progress(f"   ✓ 已保存 {anchor_count} 个助记锚点（{mnemonic_strategy}）")

    return outline


def _search_available() -> bool:
    """Return True if Google Custom Search is configured."""
    import os
    return bool(os.environ.get("SEARCH_API_KEY")) and bool(os.environ.get("SEARCH_ENGINE_ID"))


def _websearch(query: str, num_results: int = 3) -> list[dict]:
    """
    Call Google Custom Search API and return a list of {title, url, snippet}.
    Returns empty list on any error or if not configured.
    """
    import os
    try:
        import urllib.request
        import urllib.parse

        api_key = os.environ.get("SEARCH_API_KEY", "")
        engine_id = os.environ.get("SEARCH_ENGINE_ID", "")
        if not api_key or not engine_id:
            return []

        params = urllib.parse.urlencode({
            "key": api_key,
            "cx": engine_id,
            "q": query,
            "num": min(num_results, 10),
        })
        url = f"https://www.googleapis.com/customsearch/v1?{params}"

        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode())

        items = data.get("items", [])
        return [
            {"title": item.get("title", ""), "url": item.get("link", ""), "snippet": item.get("snippet", "")}
            for item in items
        ]
    except Exception as e:
        log.warning("WebSearch failed for query '%s': %s", query, e)
        return []


# ── Socratic Dialogue ──────────────────────────────────────────────────────────

def chat_turn(
    session: dict,
    node: dict,
    outline_sections: list[dict],
    user_message: str,
    history: list[dict],
    model: Optional[str] = None,
) -> tuple[str, float, list[int]]:
    """
    Process one turn of the Socratic dialogue.

    Args:
        session:          Current learning_sessions row.
        node:             The knowledge_nodes row being taught.
        outline_sections: Parsed sections list from the outline.
        user_message:     The learner's input text.
        history:          Previous chat_messages rows (role + content).
        model:            Optional model override.

    Returns:
        (response_text, progress_float, all_covered_sections)
        - response_text: assistant reply to show the user
        - progress_float: 0.0–1.0 fraction of sections covered so far
        - all_covered_sections: cumulative list of covered section indices
    """
    outline_text = _format_outline_for_prompt(outline_sections)
    system = SOCRATIC_SYSTEM.format(
        node_title=node["title"],
        node_description=node.get("description", ""),
        outline_text=outline_text,
    )

    # Build messages list for the model
    messages_for_model: list[dict] = []
    for msg in history:
        messages_for_model.append({"role": msg["role"], "content": msg["content"]})
    messages_for_model.append({"role": "user", "content": user_message})

    # Call the Socratic agent (expect JSON response with response + newly_covered_sections)
    # We pass the entire history in the user turn as a formatted transcript for Anthropic
    # (Anthropic SDK only supports alternating user/assistant, handled in client.call)
    history_text = _format_history_for_prompt(history) if history else ""
    full_user_prompt = (
        (f"## 对话历史\n{history_text}\n\n" if history_text else "")
        + f"## 学生的最新消息\n{user_message}\n\n"
        + f"## 已覆盖章节\n{json.dumps(session.get('covered_sections', []))}\n\n"
        + "请根据教学规则回复，并以 JSON 格式输出（包含 response 和 newly_covered_sections 字段）。"
    )

    try:
        result = llm.call_json(system, full_user_prompt, max_tokens=2048, model=model)
        if not isinstance(result, dict):
            raise ValueError("Unexpected response type")
        response_text = result.get("response", "")
        newly_covered = result.get("newly_covered_sections", [])
        if not isinstance(newly_covered, list):
            newly_covered = []
    except Exception as e:
        log.warning("Socratic agent JSON parse failed (%s), retrying as plain text", e)
        # Fallback: plain text response, no coverage update
        response_text = llm.call(system, full_user_prompt, max_tokens=2048, model=model)
        newly_covered = []

    if not response_text.strip():
        response_text = "（对话引擎暂时无法回复，请重试）"

    # Update covered sections — handle raw JSON string from freshly-created sessions
    raw = session.get("covered_sections", [])
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = []
    covered: list[int] = [x for x in raw if isinstance(x, int)]

    for idx in newly_covered:
        if isinstance(idx, int) and idx not in covered:
            covered.append(idx)

    total_sections = len(outline_sections)
    # Only count valid int section indices towards progress; cap at 1.0
    progress = min(1.0, len(covered) / total_sections) if total_sections > 0 else 0.0

    # Persist
    session_id = session["id"]
    db.add_chat_message(session_id, role="user", content=user_message)
    db.add_chat_message(session_id, role="assistant", content=response_text)
    db.update_session(session_id, covered_sections=covered, progress=progress)
    session["covered_sections"] = covered
    session["progress"] = progress

    log.info(
        "Chat turn done: session=%s progress=%.0f%% covered=%s",
        session_id, progress * 100, covered,
    )
    return response_text, progress, covered


def _format_outline_for_prompt(sections: list[dict]) -> str:
    lines = []
    for sec in sections:
        covered_mark = "✓" if sec.get("covered") else " "
        lines.append(f"[{covered_mark}] §{sec['index']} {sec['title']}")
        if sec.get("content"):
            # Truncate long content in the prompt to save tokens
            content_preview = sec["content"][:200] + ("..." if len(sec["content"]) > 200 else "")
            lines.append(f"    {content_preview}")
        if sec.get("analogy"):
            lines.append(f"    💡 类比：{sec['analogy']}")
        mnemonic = sec.get("mnemonic")
        if mnemonic and isinstance(mnemonic, dict) and mnemonic.get("content"):
            lines.append(f"    🧠 助记（{mnemonic.get('strategy', '')}）：{mnemonic['content']}")
    return "\n".join(lines)


def _format_history_for_prompt(history: list[dict]) -> str:
    lines = []
    for msg in history[-20:]:  # Keep last 20 messages to avoid token overflow
        role_label = "学生" if msg["role"] == "user" else "助手"
        lines.append(f"{role_label}：{msg['content']}")
    return "\n".join(lines)


# ── Session Management ─────────────────────────────────────────────────────────

def start_or_resume_session(node_id: str, outline_id: str, user_id: str = "default") -> dict:
    """Return the active session for this node, creating one if none exists."""
    session = db.get_active_session(node_id, user_id)
    if session:
        log.info("Resuming session %s for node %s (progress=%.0f%%)",
                 session["id"], node_id, session.get("progress", 0) * 100)
        return session
    session = db.create_learning_session(node_id=node_id, outline_id=outline_id, user_id=user_id)
    log.info("Created new session %s for node %s", session["id"], node_id)
    return session


# ── Interactive REPL ───────────────────────────────────────────────────────────

def run_chat_loop(
    node_id: str,
    user_id: str = "default",
    model: Optional[str] = None,
    console=None,
    user_domains: list[str] | None = None,
) -> bool:
    """
    Run the interactive Socratic dialogue loop for a node in the terminal.

    Returns True if the user completed learning (progress >= 90% and chose to proceed),
    False if they exited early.

    This function handles all Rich console output directly. If `console` is None,
    it creates a plain Console.
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, TextColumn
    from rich.rule import Rule
    from rich.markdown import Markdown

    if console is None:
        console = Console()

    node = db.get_node(node_id)
    if not node:
        console.print(f"[red]找不到节点 ID: {node_id}[/red]")
        return False

    # ── Generate / load outline ────────────────────────────────────────────────
    console.rule(f"[bold blue]📚 {node['title']}[/bold blue]")
    try:
        outline = generate_outline(
            node_id=node_id,
            user_id=user_id,
            model=model,
            user_domains=user_domains,
            progress_cb=lambda msg: console.print(f"[dim]{msg}[/dim]"),
        )
    except Exception as e:
        console.print(f"[red]大纲生成失败：{e}[/red]")
        return False

    sections = outline["sections"]

    # Display outline
    console.print()
    outline_display = "\n".join(
        f"  **§{s['index']} {s['title']}**"
        + (f"\n  > 💡 类比：{s['analogy']}" if s.get("analogy") else "")
        for s in sections
    )
    console.print(Panel(
        Markdown(outline_display),
        title="学习大纲",
        border_style="cyan",
    ))
    console.print()
    console.print("[dim]输入 /exit 退出，/progress 查看进度，/outline 重新显示大纲[/dim]")
    console.print()

    # ── Start / resume session ─────────────────────────────────────────────────
    db.update_outline(outline["id"], status="active")
    session = start_or_resume_session(node_id, outline["id"], user_id)

    # If resuming, sync covered state to sections
    covered = list(session.get("covered_sections", []))
    for sec in sections:
        sec["covered"] = sec["index"] in covered

    # Load existing chat history
    history = db.get_chat_history(session["id"], limit=100)

    if history:
        console.print(f"[dim]↩  继续上次学习（已有 {len(history)} 条对话记录）[/dim]\n")

    # ── REPL loop ──────────────────────────────────────────────────────────────
    completed = False
    while True:
        # Show progress
        current_progress = session.get("progress", 0.0)
        covered = list(session.get("covered_sections", []))
        covered_count = len(covered)
        total = len(sections)

        _print_progress(console, covered_count, total, current_progress)

        # Check if complete
        if current_progress >= 0.9 and not completed:
            console.print()
            console.print(Panel(
                f"[green bold]🎉 学习进度已达 {current_progress:.0%}！[/green bold]\n\n"
                "你已经覆盖了大纲的主要内容。\n"
                "输入 [bold]/exam[/bold] 进入考试，或继续对话深化理解。",
                border_style="green",
            ))
            completed = True

        # Get user input
        try:
            user_input = input("\n你：").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]已退出学习。[/yellow]")
            break

        if not user_input:
            continue

        # Handle slash commands
        if user_input.lower() in ("/exit", "/quit"):
            console.print("[yellow]已退出学习。[/yellow]")
            break

        if user_input.lower() == "/exam":
            console.print("[green]即将进入考试...[/green]")
            db.update_outline(outline["id"], status="completed")
            db.update_session(session["id"], covered_sections=covered, progress=current_progress, status="completed")
            return True  # Signal to proceed to exam

        if user_input.lower() == "/progress":
            _print_sections_detail(console, sections, covered)
            continue

        if user_input.lower() == "/outline":
            console.print(Panel(Markdown(outline_display), title="学习大纲", border_style="cyan"))
            continue

        # Chat turn
        try:
            response_text, progress, covered = chat_turn(
                session=session,
                node=node,
                outline_sections=sections,
                user_message=user_input,
                history=history,
                model=model,
            )
        except Exception as e:
            log.error("Chat turn failed: %s", e)
            console.print(f"[red]对话出错：{e}[/red]")
            continue

        # Update local sections covered state
        for sec in sections:
            sec["covered"] = sec["index"] in session["covered_sections"]

        # Append to local history for context
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": response_text})

        # Display response
        console.print()
        console.print(Panel(Markdown(response_text), title="助手", border_style="blue", padding=(1, 2)))

    return completed


def _print_progress(console, covered_count: int, total: int, progress: float):
    """Print a compact progress bar."""
    filled = int(progress * 20)
    bar = "█" * filled + "░" * (20 - filled)
    color = "green" if progress >= 0.9 else ("yellow" if progress >= 0.5 else "white")
    console.print(
        f"[dim]学习进度：[/{color}][{color}]{bar}[/{color}][dim] {progress:.0%} "
        f"({covered_count}/{total} 节)[/dim]"
    )


def _print_sections_detail(console, sections: list[dict], covered: list[int]):
    """Print a detailed section-by-section coverage view."""
    console.print()
    for sec in sections:
        icon = "✓" if sec["index"] in covered else "○"
        color = "green" if sec["index"] in covered else "dim"
        console.print(f"  [{color}]{icon} §{sec['index']} {sec['title']}[/{color}]")
    console.print()
