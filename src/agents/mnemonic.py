"""
File: agents/mnemonic.py

Purpose:
    Cognitive preference assessment and mnemonic strategy support.
    Provides the assessment questions, weight computation, and prompt
    builder used by the teacher agent to generate strategy-specific
    mnemonic anchors during outline generation and Socratic dialogue.

Responsibilities:
    - Define a fixed set of cognitive assessment questions (3-4 scenario-based)
    - Compute cognitive preference weights from user answers
    - Determine the dominant mnemonic strategy
    - Build prompt snippets that instruct the LLM to generate mnemonic content
      matching the user's preferred encoding style

What this file does NOT do:
    - Database operations (callers handle persistence via db.create_cognitive_profile)
    - CLI presentation (that lives in cli/main.py)
    - Outline generation or Socratic dialogue (that lives in agents/teacher.py)

Key Design Decisions:
    - Assessment uses a fixed question set (no LLM call needed), making it fast,
      deterministic, and free of API costs
    - Weights always sum to 1.0 and have a minimum floor of 0.05 per strategy
      to avoid completely ignoring any encoding style
    - Prompt snippets are in Chinese to match the existing agent prompt language

Inputs:
    - User's answer choices (list of strategy strings)

Outputs:
    - Cognitive weights dict: {spatial, symbolic, narrative}
    - Prompt snippet string for injection into teacher agent prompts
"""

from typing import Optional

# ── Assessment Questions ──────────────────────────────────────────────────────

# Each question has 3 options, one per strategy. The user picks A/B/C which
# maps to spatial/symbolic/narrative (order is shuffled per question to reduce
# bias, but the strategy tag is what matters).

ASSESSMENT_QUESTIONS = [
    {
        "prompt": "你要记住一个新同事的名字和部门，你更可能怎么做？",
        "options": [
            {"label": "A. 在脑中把他的脸和办公室的某个位置联系起来", "strategy": "spatial"},
            {"label": "B. 把名字和部门归类到一个已知的分组里（比如'技术部三个人'）", "strategy": "symbolic"},
            {"label": "C. 编一个小故事，比如'小王从技术部跑来送文件'", "strategy": "narrative"},
        ],
    },
    {
        "prompt": "学一个复杂的新概念时，什么方式最能帮你理解？",
        "options": [
            {"label": "A. 画一张图或在脑中构建一个场景来表示各部分的关系", "strategy": "spatial"},
            {"label": "B. 把它拆成几条规则或一个逻辑链：如果X则Y，因为Z", "strategy": "symbolic"},
            {"label": "C. 用一个现实中的故事或比喻来串联各个要点", "strategy": "narrative"},
        ],
    },
    {
        "prompt": "复习时，你发现自己最容易回忆起的是？",
        "options": [
            {"label": "A. 当时看到的画面、图表、或信息在页面上的位置", "strategy": "spatial"},
            {"label": "B. 概念之间的逻辑关系和分类结构", "strategy": "symbolic"},
            {"label": "C. 学习时想到的那个例子或故事", "strategy": "narrative"},
        ],
    },
    {
        "prompt": "如果要你记住一个包含 7 个步骤的流程，你倾向于？",
        "options": [
            {"label": "A. 想象自己走过 7 个房间，每个房间里放一个步骤", "strategy": "spatial"},
            {"label": "B. 找到步骤之间的因果关系，编成一条规则链", "strategy": "symbolic"},
            {"label": "C. 把 7 个步骤编进一个有情节的小故事里", "strategy": "narrative"},
        ],
    },
]


def get_assessment_questions() -> list[dict]:
    """Return the fixed set of cognitive preference assessment questions."""
    return ASSESSMENT_QUESTIONS


# ── Weight Computation ────────────────────────────────────────────────────────

_MIN_WEIGHT = 0.05  # minimum floor per strategy


def compute_weights(answers: list[str]) -> dict[str, float]:
    """
    Compute cognitive preference weights from a list of strategy answers.

    Each answer is one of: 'spatial', 'symbolic', 'narrative'.
    Returns dict with keys spatial/symbolic/narrative, values summing to 1.0.
    Applies a minimum floor of 0.05 per strategy.
    Empty answers → uniform distribution.
    """
    if not answers:
        return {"spatial": 0.33, "symbolic": 0.33, "narrative": 0.34}

    counts = {"spatial": 0, "symbolic": 0, "narrative": 0}
    for a in answers:
        if a in counts:
            counts[a] += 1

    total = sum(counts.values())
    if total == 0:
        return {"spatial": 0.33, "symbolic": 0.33, "narrative": 0.34}

    # Raw proportions
    raw = {k: v / total for k, v in counts.items()}

    # Apply floor: ensure every strategy has at least _MIN_WEIGHT,
    # then redistribute the excess proportionally from the others.
    num_strategies = len(raw)
    floor_total = _MIN_WEIGHT * num_strategies  # total reserved for floors

    # Clamp to floor and redistribute
    deficit = 0.0
    above_floor = []
    for k in raw:
        if raw[k] < _MIN_WEIGHT:
            deficit += _MIN_WEIGHT - raw[k]
            raw[k] = _MIN_WEIGHT
        else:
            above_floor.append(k)

    # Take deficit from strategies above the floor proportionally
    if deficit > 0 and above_floor:
        above_total = sum(raw[k] for k in above_floor)
        for k in above_floor:
            raw[k] -= deficit * (raw[k] / above_total)

    # Normalise to exactly 1.0
    total_raw = sum(raw.values())
    weights = {k: v / total_raw for k, v in raw.items()}

    return weights


# ── Dominant Strategy ─────────────────────────────────────────────────────────

def get_dominant_strategy(weights: dict[str, float]) -> str:
    """Return the strategy with the highest weight."""
    return max(weights, key=weights.get)


# ── Prompt Builder ────────────────────────────────────────────────────────────

_SPATIAL_SNIPPET = """\
## 助记策略：空间-视觉编码
学习者偏好通过空间场景和视觉画面来记忆。请为每个 section 额外生成一个 mnemonic 字段：
- "mnemonic": {"strategy": "spatial", "content": "一段生动的空间/场景描述，把该小节的核心概念放入一个具体的位置或画面中", "palace_location": "该概念在想象空间中的位置名称"}
- 场景要具体、夸张、有画面感（比如"想象走进一栋银行大楼，门口保安要求你签名"）
- palace_location 用简短的位置名（比如"一楼大厅入口"、"二楼会议室"）
"""

_SYMBOLIC_SNIPPET = """\
## 助记策略：符号-逻辑编码
学习者偏好通过逻辑规则和分类结构来记忆。请为每个 section 额外生成一个 mnemonic 字段：
- "mnemonic": {"strategy": "symbolic", "content": "一条逻辑规则链、分类口诀或首字母缩写，概括该小节的核心知识", "palace_location": null}
- 内容应是精炼的规则（比如"先写日志 → 再执行 → 崩溃重放"）或分类树（比如"三类隔离级别：读未提交/读已提交/可重复读"）
- 强调因果关系和模式，避免叙事性描述
"""

_NARRATIVE_SNIPPET = """\
## 助记策略：叙事编码
学习者偏好通过故事和情节来记忆。请为每个 section 额外生成一个 mnemonic 字段：
- "mnemonic": {"strategy": "narrative", "content": "一个短小的故事或情境，把该小节的核心概念编入有角色、有情节的叙事中", "palace_location": null}
- 故事要有角色和动作（比如"银行柜员小王每天上班第一件事就是打开日志本……"）
- 用因果情节串联知识点，让故事本身就能帮助回忆
"""

_SNIPPETS = {
    "spatial": _SPATIAL_SNIPPET,
    "symbolic": _SYMBOLIC_SNIPPET,
    "narrative": _NARRATIVE_SNIPPET,
}


def build_mnemonic_prompt_snippet(strategy: str) -> str:
    """
    Return a prompt snippet to inject into the outline generation system prompt.

    If the strategy is unknown, returns empty string (mnemonic layer disabled).
    """
    return _SNIPPETS.get(strategy, "")
