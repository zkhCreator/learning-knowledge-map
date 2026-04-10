# 10 — 助记策略层（Mnemonic Strategy Layer）

> **版本**: v0.1 | **日期**: 2026-04-09 | **状态**: 设计确认

---

## 一句话描述

在现有学习流程上叠加一个**可插拔的助记策略层**，根据用户的认知偏好，在学习和复习阶段自动选择最合适的记忆编码方式（记忆宫殿、逻辑链、叙事编码等）。

## 设计理念

记忆宫殿本质上是一种**编码策略**——把信息从一种表征转换成另一种更容易被大脑提取的表征。不同的人适合不同的编码方式：有的人偏向**视觉-空间编码**（在脑中构建场景和位置），有的人偏向**语言-符号编码**（通过逻辑关系和分类体系记忆）。硬编码一种策略会让系统只对部分用户有效。

因此我们设计一个策略框架，让系统根据用户画像选择策略、生成助记内容、在复习时复用这些内容。

**核心原则：助记层是增强，不是替代。** 用户完全可以关掉它，系统回退到现有的纯 Q&A 流程，不影响任何已有功能。

---

## 认知偏好模型

定义三种主要认知编码类型：

| 类型 | 代号 | 特征 | 对应策略 |
|------|------|------|----------|
| 空间-视觉型 | `spatial` | 擅长在脑中构建场景、位置、画面 | 记忆宫殿、心理图像 |
| 符号-逻辑型 | `symbolic` | 擅长分类、规则链、模式归纳 | 逻辑链、分类树、口诀/缩写 |
| 叙事型 | `narrative` | 擅长通过故事情节串联信息 | 故事编码、因果链叙事 |

不做互斥，用**权重**表示倾向。比如一个用户可能是 `spatial: 0.6, symbolic: 0.3, narrative: 0.1`。

### 偏好获取方式

1. **初始评估（轻量）** — 用户首次使用时，问 3-4 个简单的场景题。不是心理测试，而是具体场景选择，例如"你要记住一个新同事的名字，你更可能怎么做？A. 把他的脸和一个画面联系起来 B. 把名字拆成有意义的部分 C. 编一个关于他的小故事"。
2. **动态调整** — 使用助记策略复习时，如果某种策略下的复习得分持续高于其他策略，自动调高该策略的权重。
3. **手动覆盖** — 用户随时可以通过 CLI 切换或关闭助记功能。

---

## 三种策略的具体行为

### 策略 A：记忆宫殿（spatial）

作用于两个层面：

- **节点内（micro）**：学习一个知识点时，Agent 为每个大纲小节生成一个空间意象锚点。比如学 WAL 日志，Agent 建议"想象走进一栋银行大楼，门口有个保安要求每个人进去前先在登记簿上签名——这就是 Write-Ahead 的含义"。
- **目标级（macro）**：一个 learning_goal 的 DAG 映射为一座建筑/路线。拓扑排序决定"房间"顺序，前置依赖决定哪些门先开。Agent 在目标初始化时生成一段宫殿布局描述，复习时引导用户"从入口走到你上次停下的房间"。

### 策略 B：逻辑链（symbolic）

- **节点内**：为每个知识点提取核心规则链（"如果X → 则Y → 导致Z"），或生成首字母缩写/口诀。
- **目标级**：用分类树的形式组织整个 DAG——"这个目标分为3个大类，每个大类下有N个子规则"。强调模式复用，比如"你之前学过的X模式在这里又出现了，规则完全一样，只是作用对象不同"。

### 策略 C：叙事编码（narrative）

- **节点内**：把大纲内容编成一个连贯的小故事。比如学数据库事务："想象你是一个银行柜员，今天来了一个客户要同时转账和取款……"
- **目标级**：整个学习目标变成一个"冒险故事"，每学完一个节点解锁故事的下一章。

---

## 与现有模块的集成点

### 1. 大纲生成（→ 06-node-learning-flow）

现有的大纲数据结构里每个 section 已经有 `analogy` 字段。扩展为：

```json
{
  "index": 1,
  "title": "WAL 日志的基本原理",
  "content": "...",
  "analogy": "类似于写代码前先记录修改计划",
  "analogy_source_node": "版本控制基础",
  "mnemonic": {
    "strategy": "spatial",
    "content": "想象银行大楼入口，保安要求先签登记簿...",
    "palace_location": "一楼大厅入口"
  }
}
```

`mnemonic` 字段根据用户偏好由 Agent 生成。如果用户偏好是 symbolic，`strategy` 就是 `"symbolic"`，`content` 就是逻辑链或口诀。`palace_location` 仅 spatial 策略使用，其他策略该字段为 null。

### 2. 苏格拉底对话（学习阶段）

Agent prompt 里加入当前用户的认知偏好，对话时自然地融入助记策略。不是每轮都提，而是在以下时机触发：

- 引入新概念时
- 用户表示困惑时
- 覆盖一个新大纲小节时

### 3. 考试阶段

**不变。** 考试应该测试真正的理解，不测助记线索本身。

### 4. 复习阶段（→ 05-ebbinghaus-scheduler）

新增一个可选的复习步骤——在 Q&A 之前，先做一轮**助记回忆（Mnemonic Retrieval）**：

- spatial 用户："你上次把 WAL 的概念放在了银行大厅入口，现在回忆一下那个场景，你看到了什么？"
- symbolic 用户："WAL 的核心规则链是什么？按顺序说出三个步骤。"
- narrative 用户："还记得那个银行柜员的故事吗？接下来发生了什么？"

这一步**不打分**，纯粹作为提取练习（retrieval practice），然后正常进入 Q&A。

### 5. 错题本（→ 06-node-learning-flow）

错题归因里新增一个类型：

- `mnemonic_failure`: 用户记住了助记线索但搞混了对应的真实概念

这种情况说明助记策略本身需要调整（换一个更好的锚点）。

---

## 数据结构变更

### 新增表

```sql
-- 用户认知偏好
CREATE TABLE user_cognitive_profile (
    user_id          TEXT PRIMARY KEY,
    spatial_weight   REAL DEFAULT 0.33,
    symbolic_weight  REAL DEFAULT 0.33,
    narrative_weight REAL DEFAULT 0.34,
    assessed         BOOLEAN DEFAULT FALSE,
    -- FALSE = 未做初始评估，使用默认均匀权重
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 助记锚点记录
CREATE TABLE mnemonic_anchors (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    node_id         TEXT NOT NULL REFERENCES knowledge_nodes(id),
    strategy        TEXT NOT NULL,
    -- 'spatial' | 'symbolic' | 'narrative'
    section_index   INTEGER,
    -- 对应大纲的哪个小节，NULL 表示目标级助记
    content         TEXT NOT NULL,
    -- 助记内容的文字描述
    palace_location TEXT,
    -- 仅 spatial 策略使用，描述在宫殿中的位置
    effectiveness   REAL,
    -- 复习时动态更新，衡量该锚点的有效性（0.0-1.0）
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, node_id, section_index)
);

-- 宫殿布局（仅 spatial 策略）
CREATE TABLE palace_layouts (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    goal_id         TEXT NOT NULL REFERENCES learning_goals(id),
    layout_desc     TEXT NOT NULL,
    -- Agent 生成的空间布局描述（纯文字）
    location_map    TEXT,
    -- JSON: { node_id: "位置名称" } 的映射
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, goal_id)
);
```

### 已有表的影响

- `knowledge_nodes`: 无需改动，助记内容存在 `mnemonic_anchors` 表
- `user_knowledge_state`: 无需改动
- `review_schedule`: 无需改动，助记回忆步骤是 Agent 行为层面的变化，不影响调度逻辑

---

## Non-Goals（不做什么）

- **不做图形化的宫殿渲染** — CLI 阶段，助记内容全部是文字描述，靠用户自己在脑中构建画面。后续 Web UI 阶段可以考虑可视化。
- **不强制使用** — 助记层完全可选，`assessed = FALSE` 且用户不主动开启时，系统行为和现在完全一样。
- **不替代艾宾浩斯调度** — 助记策略改善的是编码质量，艾宾浩斯管的是复习时机，两者互补不冲突。
- **不做科学严谨的认知测评** — 初始评估是轻量级的倾向判断，不是心理学量表。
- **不做多策略并行** — 同一个节点的同一个小节，只生成一种策略的助记内容（按权重最高的策略选择），不同时生成多套。

---

## 实现优先级

| 阶段 | 内容 | 复杂度 |
|------|------|--------|
| P0 | `user_cognitive_profile` 表 + 初始评估（3-4 题） | 低 |
| P0 | Agent prompt 改造：根据偏好在大纲生成时加入 `mnemonic` 字段 | 中 |
| P1 | `mnemonic_anchors` 表 + 助记内容持久化 | 低 |
| P1 | 苏格拉底对话中融入助记引导 | 中 |
| P1 | 复习阶段的"助记回忆"步骤 | 中 |
| P2 | `palace_layouts` 表 + 目标级空间映射（仅 spatial） | 中 |
| P2 | 助记有效性追踪 + 动态调整认知偏好权重 | 中 |
| P3 | 错题归因新增 `mnemonic_failure` 类型 | 低 |

---

## Common Questions

**Q: 助记策略会不会增加 Agent 调用次数？**
A: 不会额外增加。助记内容在大纲生成时一并产出（同一次 Agent 调用），复习时的助记回忆也是同一次对话的一部分。

**Q: 如果用户觉得助记内容不好怎么办？**
A: 用户可以手动标记某个锚点无效，系统会重新生成。这也会影响 `effectiveness` 评分和认知偏好权重的动态调整。

**Q: spatial 策略的宫殿布局是谁决定的？**
A: 系统根据 DAG 拓扑自动生成布局建议（Agent 完成）。用户不需要自己设计宫殿。后续可以考虑让用户选择自己熟悉的场景作为宫殿模板。
