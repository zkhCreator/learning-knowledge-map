# 递归学习图谱引擎 — 系统设计文档

> **版本**: v0.1 (设计讨论稿)
> **日期**: 2026-04-06
> **状态**: 架构讨论阶段，尚未开始实现

---

## 1. 系统概览

### 1.1 一句话描述

用户设定学习目标 → 系统递归拆解成原子知识点 → 构建有向图 → 评估用户已知节点 → 计算最短学习路径 → 逐节点教学+验证 → 艾宾浩斯调度复习。

### 1.2 核心设计理念

- **递归拆解 + 双 Agent 质量保证**: 每一层拆解都有正向 Agent（出方案）和反向 Agent（review 方案），保证每一步稳定可靠
- **知识图谱驱动**: 所有知识点构成有向无环图（DAG），支持跨目标、跨领域的知识复用和类比关联
- **用户已知路径优先**: 评估用户已有知识后，计算最短学习路径，跳过已掌握内容
- **艾宾浩斯记忆曲线**: 掌握的知识按遗忘曲线安排复习，mastery_score 随时间自然衰减
- **分级通过标准**: 不同类型的知识有不同的掌握阈值（医疗等高风险领域要求更严格）

### 1.3 技术栈（现阶段）

| 组件       | 实现方案                                      |
| ---------- | --------------------------------------------- |
| 存储       | SQLite（知识图谱 + 用户状态 + 复习调度）       |
| AI 调用    | Anthropic 或 OpenAI-compatible，通过自定义 domain / 代理连接 |
| 交互       | CLI                                           |
| Agent 编排 | 本地脚本，正向 + 反向 Agent 串联调用           |

---

## 2. 模块一：递归目标拆解器（Plan Decomposer）

### 2.1 核心流程

```
用户输入目标（如："学会 Kubernetes 集群管理"）
         │
         ▼
    ┌─────────────┐
    │  正向 Agent  │  将目标拆解成 N 个子目标
    └──────┬──────┘
           ▼
    ┌─────────────┐
    │  反向 Agent  │  Review: 是否遗漏？粒度够细？依赖关系对不对？
    └──────┬──────┘
           ▼
      ┌─────────┐
      │  通过？  │── 否 → 带着反馈重新拆解（回到正向 Agent）
      └────┬────┘
           │ 是
           ▼
     对每个子目标递归执行同样的流程
           │
           ▼
     终止：节点满足原子条件
       (QA 可验证 + 短时间可学完)
```

### 2.2 正向 Agent

**职责**: 将当前目标拆解成子目标/子知识点。

**输入**:
- 当前节点的上下文（父目标、在整棵树中的位置）
- 用户画像（已知领域，方便生成跨域类比）
- 拆解规则

**输出**:
- 子节点列表（名称 + 描述）
- 子节点之间的依赖关系（边）
- 每个子节点的预估学习时间
- 每个子节点的验证 QA 草案
- 每个子节点的概念指纹（用于跨域关联）
- 每个子节点的严格度等级建议

### 2.3 反向 Agent

**职责**: Review 拆解方案的可靠性，确保流程中每一步都稳定可靠。

**Review 维度（Checklist）**:
1. **完备性**: 拆解是否覆盖了目标所需的所有知识？
2. **粒度合适性**: 太粗则要求继续拆分，太细则建议合并
3. **依赖正确性**: A 真的是 B 的前置吗？是否存在循环依赖？
4. **跨域前置**: 是否遗漏了其他领域的前置知识？（如学 K8s 网络前需要 Linux 网络基础）
5. **严格度标注**: critical/standard/familiarity 的标注是否合理？

**输出**: 通过/不通过 + 具体修改建议

### 2.4 递归终止条件

一个节点被认为是「原子知识点」需**同时满足**:
1. 能生成至少 3 个有区分度的 QA 问题
2. 预估学习时间不超过阈值（建议 15 分钟）
3. 不存在需要进一步拆分的子概念

反向 Agent 负责判定这三个条件是否满足。

### 2.5 递归深度控制

- 最大递归深度: 5-7 层
- 到达深度上限时强制标记为原子节点
- 记录强制终止标记，后续可人工审查

---

## 3. 模块二：知识有向图（Knowledge DAG）

### 3.1 核心概念

- **节点**: 原子知识点，带有元信息（领域、难度、学习时间、QA 集、概念指纹）
- **前置依赖边**: A → B 表示学 B 之前必须掌握 A
- **跨域关联边**: 不同领域之间的类比/关联关系，附带类比说明

### 3.2 SQLite Schema

```sql
-- 学习目标
CREATE TABLE learning_goals (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    title           TEXT NOT NULL,
    root_node       TEXT REFERENCES knowledge_nodes(id),
    status          TEXT NOT NULL DEFAULT 'decomposing',
    -- 'decomposing' | 'active' | 'completed'
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 知识节点
CREATE TABLE knowledge_nodes (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    description         TEXT,
    domain              TEXT,
    concept_fingerprint TEXT,
    -- JSON array: 抽象模式标签，如 ["隔离性", "原子操作", "状态一致性"]
    difficulty          INTEGER CHECK (difficulty BETWEEN 1 AND 5),
    est_minutes         INTEGER,
    qa_set              TEXT,
    -- JSON: [{"question": "...", "expected_answer": "...", "difficulty": 1-5}]
    depth_level         INTEGER,
    parent_goal         TEXT REFERENCES learning_goals(id),
    strictness_level    TEXT NOT NULL DEFAULT 'standard',
    -- 'critical' | 'standard' | 'familiarity'
    mastery_threshold   REAL NOT NULL DEFAULT 0.80,
    -- critical: 0.95, standard: 0.80, familiarity: 0.60
    risk_note           TEXT,
    -- 对于 critical 类型，说明错误理解的风险
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 知识边（依赖关系）
CREATE TABLE knowledge_edges (
    id              TEXT PRIMARY KEY,
    from_node       TEXT NOT NULL REFERENCES knowledge_nodes(id),
    to_node         TEXT NOT NULL REFERENCES knowledge_nodes(id),
    edge_type       TEXT NOT NULL DEFAULT 'prerequisite',
    -- 'prerequisite' | 'cross_domain_analogy'
    weight          REAL DEFAULT 1.0,
    analogy_desc    TEXT,
    -- 跨域关联时的类比说明
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(from_node, to_node)
);

-- 用户知识状态
CREATE TABLE user_knowledge_state (
    user_id         TEXT NOT NULL,
    node_id         TEXT NOT NULL REFERENCES knowledge_nodes(id),
    status          TEXT NOT NULL DEFAULT 'unknown',
    -- 'unknown' | 'learning' | 'assessed' | 'mastered' | 'needs_review'
    raw_score       REAL DEFAULT 0.0,
    -- 最近一次 QA 的原始得分 (0.0 - 1.0)
    stability       REAL DEFAULT 1.0,
    -- 遗忘速率参数，越大遗忘越慢，随成功复习次数增长
    last_reviewed   TIMESTAMP,
    next_review     TIMESTAMP,
    review_count    INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, node_id)
);

-- effective_mastery 不存数据库，查询时动态计算:
-- effective_mastery = raw_score × exp(-days_elapsed / stability)

-- 复习调度表
CREATE TABLE review_schedule (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    node_id             TEXT NOT NULL REFERENCES knowledge_nodes(id),
    scheduled_at        TIMESTAMP NOT NULL,
    actual_at           TIMESTAMP,
    review_round        INTEGER NOT NULL,
    score               REAL,
    next_interval_days  INTEGER,
    status              TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' | 'completed' | 'overdue'
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.3 跨域关联：概念指纹机制

**原理**: 正向 Agent 在生成每个知识节点时，同时提取 2-3 个「抽象模式标签」作为概念指纹。

**示例**:
- "数据库事务" → `["隔离性", "原子操作", "状态一致性"]`
- "React useEffect" → `["副作用管理", "生命周期", "依赖追踪"]`
- "保险精算" → `["风险量化", "概率模型", "时间衰减"]`
- "分布式共识算法" → `["状态一致性", "容错", "多数派决策"]`

**关联发现**: 当用户跨领域学习时，系统对比新旧节点的概念指纹，发现共同的抽象模式（如"状态一致性"同时出现在数据库和分布式系统中），生成 `cross_domain_analogy` 边并附上类比说明。

**实现方式（现阶段）**: 不需要 embedding 向量检索，直接用 Agent 对比两个节点的 concept_fingerprint 即可。

### 3.4 跨目标图谱融合

用户学习多个目标后，各自的 DAG 逐渐融合成一张大图。之前学目标 A 时掌握的节点，可能直接成为目标 B 的前置，从而缩短新目标的学习路径。

---

## 4. 模块三：学习路径算法

### 4.1 初始评估（粗粒度 + 动态校准）

**初始化时**:
- 从 DAG 中选出各层级的「探测节点」（每层 1-2 个代表性节点）
- 对探测节点执行快速 QA 测试
- 根据回答质量批量标记状态
  - 答对了某节点 → 该节点的前置节点大概率已掌握，批量标记
  - 答错了 → 该节点及其后续节点标记为 unknown

**学习过程中持续校准**:
- 每完成一个节点的 QA 验证，动态调整关联节点的状态估计
- 用户表现超预期 → 可跳过后续简单节点
- 用户卡住 → 检查是否有未标记的前置知识缺口，动态插入补课节点

### 4.2 最短路径计算

**算法**:
1. 将 DAG 中所有 `status = mastered` 且 `effective_mastery >= mastery_threshold` 的节点标记为"已到达"
2. 目标节点为终点
3. 执行拓扑排序 + 最短路径算法
4. 路径权重 = `sum(est_minutes)`，按学习时间最优化

**输出**: 一条从用户当前知识状态到目标的最优学习序列。

### 4.3 分支知识处理

- 最短路径 = 关键路径（必须学）
- 分支知识 = 不在关键路径上但能加深理解的节点
- 标记为「推荐但非必须」，由用户决定是否扩展
- 在 CLI 中以可选项形式呈现

### 4.4 动态调整

学习过程中路径不是固定的，每完成一个节点后可能触发路径重算:
- 发现新的知识缺口 → 插入补课节点
- 用户展现出超预期的能力 → 跳过简单节点
- 跨域关联被触发 → 提供类比捷径

---

## 5. 模块四：艾宾浩斯复习调度

### 5.1 复习间隔模型

基础间隔表:

| 复习轮次 | 1 | 2 | 3 | 4  | 5  | 6  |
| -------- | - | - | - | -- | -- | -- |
| 间隔天数 | 1 | 3 | 7 | 14 | 30 | 90 |

根据掌握质量动态调整:
- **优秀** (score >= 0.8): 正常推进到下一个间隔
- **一般** (0.5 - 0.8): 间隔缩短 50%
- **差** (< 0.5): 状态退回 `needs_review`，从间隔 1 重新开始，正向 Agent 重新讲解

### 5.2 mastery_score 衰减机制

```
effective_mastery = raw_score × exp(-days_elapsed / stability)
```

- `raw_score`: 最近一次 QA 的原始得分
- `days_elapsed`: 距上次复习的天数
- `stability`: 遗忘速率参数
  - 初始值 = 1.0
  - 每次成功复习（score >= threshold）后增长: `stability *= 1.5`
  - 复习失败后衰减: `stability *= 0.7`

**节点完成判定**:
```
is_node_complete(node, user):
    effective = raw_score × exp(-days_elapsed / stability)
    return effective >= node.mastery_threshold
```

这统一了两个需求: mastery_score 随时间衰减 + 不同节点有不同完成标准。

### 5.3 严格度分级与阈值

| 等级         | 名称     | mastery_threshold | 适用场景                           |
| ------------ | -------- | ----------------- | ---------------------------------- |
| `critical`   | 严格掌握 | >= 0.95           | 医疗、安全、法律合规等高风险领域     |
| `standard`   | 标准掌握 | >= 0.80           | 大多数技术知识、学科概念             |
| `familiarity`| 了解即可 | >= 0.60           | 背景知识、概览性内容                |

**对 critical 节点的特殊处理**:
- QA 验证更严格，反向 Agent 设计易混淆的陷阱题
- 未达阈值时不允许进入后续节点
- 复习频率更高（间隔缩短 50%）

**阈值来源**: 正向 Agent 拆解时自动标注（检测到高风险领域自动标 critical）+ 用户可手动覆盖。

### 5.4 CLI 会话中的复习优先级

每次 CLI 会话开始时，系统检查复习队列:

```
优先级排序:
1. 逾期复习（overdue） — 尤其是 critical 节点
2. 今日复习（pending, scheduled_at <= today）
3. 继续新知识学习
```

这保证遗忘曲线不会被新内容的学习打断。

---

## 6. 节点级学习流程（学习 + 考试）

每个原子节点分为 **学习阶段** 和 **考试阶段**。用户可以跳过学习直接进入考试。

### 6.1 整体流程

```
用户进入节点
    │
    ├── [选择] 开始学习  ─────────────────────────────────────┐
    │                                                         │
    │   ┌──────────────────┐                                  │
    │   │ 1. 生成知识大纲   │  正向 Agent + WebSearch 搜索来源  │
    │   └────────┬─────────┘                                  │
    │            ▼                                            │
    │   ┌──────────────────┐                                  │
    │   │ 2. 反向 Agent 校验│  检查大纲知识点的准确性           │
    │   └────────┬─────────┘                                  │
    │            ▼                                            │
    │   ┌──────────────────┐                                  │
    │   │ 3. 苏格拉底对话   │  围绕大纲交互学习（追踪进度 %）   │
    │   └────────┬─────────┘                                  │
    │            ▼                                            │
    │   ┌──────────────────┐                                  │
    │   │ 4. 进度 >= 90%   │  AI 主动发起总结，建议进入考试     │
    │   └────────┬─────────┘                                  │
    │            │                                            │
    ├── [选择] 跳过学习，直接考试 ─────────────────────────────┘
    │                                                         │
    ▼                                                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 考试阶段（Q&A）                                               │
│  - 基于大纲出题，可适当扩展但不超出太多                         │
│  - 评分 + 错误归因分析                                        │
│  - 错题整理 → 错题本（来源知识点 + 错误回答 + 正确答案 + 关联）  │
│  - 达标 → mastered + 艾宾浩斯队列                             │
│  - 未达标 → 定位薄弱知识点，建议重新学习                       │
└──────────────────────────────────────────────────────────────┘
```

### 6.2 学习阶段 — 大纲生成（Outline Generation）

用户点击"学习"后，系统为该知识点生成结构化大纲：

**流程：**
1. 正向 Agent 根据节点 title/description 生成大纲（3-8 个小节）
2. 对每个小节，使用 **WebSearch** 搜索论文/权威知识来源，附加引用
3. 利用用户已知领域进行 **类比桥接**：每个小节如果能关联到用户已掌握的知识，生成类比说明
4. 反向 Agent 对大纲进行 **准确性校验**：检查知识点是否正确、是否过时、引用是否可靠
5. 校验通过后持久化到 DB

**大纲数据结构：**
```json
{
  "node_id": "xxx",
  "sections": [
    {
      "index": 1,
      "title": "WAL 日志的基本原理",
      "content": "Write-Ahead Logging 的核心思想是...",
      "sources": [
        {"title": "ARIES: A Transaction Recovery Method...", "url": "https://..."},
        {"title": "PostgreSQL WAL Internals", "url": "https://..."}
      ],
      "analogy": "类似于你写代码前先在草稿纸上记录修改计划——先写日志，再执行操作",
      "analogy_source_node": "版本控制基础",
      "covered": false
    }
  ]
}
```

### 6.3 学习阶段 — 苏格拉底式对话（Socratic Dialogue）

大纲生成后，用户进入与 Agent 的交互式学习：

**对话规则：**
- Agent 围绕大纲内容进行苏格拉底式引导：不直接告诉答案，而是通过提问引导用户思考
- 每轮对话后，系统判断当前讨论覆盖了大纲中哪些小节
- 实时更新学习进度百分比（已覆盖小节数 / 总小节数）
- 当进度 >= 90% 时，Agent 主动发起总结并建议进入考试

**进度追踪：**
```
学习进度: ████████░░ 80% (4/5 节已覆盖)
  ✓ 1. WAL 日志的基本原理
  ✓ 2. Checkpoint 机制
  ✓ 3. 崩溃恢复流程
  ✓ 4. WAL 的性能权衡
  ○ 5. WAL 在分布式系统中的变体
```

**类比桥接的使用时机：**
- 当用户对某个概念表示困惑时
- 当引入新概念时，先通过类比建立直觉
- 当用户已知领域中有高度相似的模式时

### 6.4 考试阶段（Exam）

**出题规则：**
- 主体：基于大纲内容出题（每个小节至少 1 题）
- 扩展：可适当扩展到关联知识（通过 knowledge_edges 获取），但扩展题不超过总题数的 20%
- 题型：混合（选择题、简答题、情境应用题、辨析题）
- critical 节点：增加易混淆的陷阱题

**评分 + 错误归因：**
每道题答完后：
1. 打分（0.0 - 1.0）
2. 如果答错，分析错误类型：
   - `memory_confusion`: 记忆混淆（记成了另一个概念）
   - `boundary_unclear`: 概念边界不清（大方向对但细节错）
   - `fundamental_misunderstanding`: 根本性误解（底层逻辑理解反了）
   - `incomplete`: 不完整（知道一部分但遗漏关键点）

### 6.5 错题本（Error Notebook）

每次考试的错误答案自动整理进错题本：

```
┌───────────────────────────────────────────────────┐
│ 错题本 #17                                         │
│ 来源节点: WAL 日志与事务恢复                        │
│ 来源大纲: §3 崩溃恢复流程                           │
│ 错误类型: boundary_unclear                          │
│                                                    │
│ 问题: REDO 和 UNDO 日志的区别是什么？               │
│                                                    │
│ 你的回答:                                           │
│   REDO 用于回滚未完成的事务，UNDO 用于重做已提交事务 │
│                                                    │
│ 正确答案:                                           │
│   恰好相反——REDO 重做已提交的事务，                  │
│   UNDO 回滚未完成的事务                             │
│                                                    │
│ 关联知识点: [事务 ACID 特性] [Checkpoint 机制]       │
│ 记录时间: 2026-04-07                                │
└───────────────────────────────────────────────────┘
```

错题本在艾宾浩斯复习时会被优先调用——复习一个节点时，先重做该节点的历史错题。

---

## 7. 整体数据流

```
┌──────────┐    ┌──────────────────┐    ┌──────────────┐
│ 用户输入  │───▶│ 递归拆解器        │───▶│ 知识 DAG      │
│ 学习目标  │    │ (正向+反向 Agent) │    │ (SQLite)     │
└──────────┘    └──────────────────┘    └──────┬───────┘
                                               │
                                               ▼
┌──────────┐    ┌──────────────────┐    ┌──────────────┐
│ CLI 交互  │◀──│ 路径算法          │◀──│ 用户知识状态  │
│ 学习+复习 │    │ (最短路径+动态)   │    │ (初始+动态)  │
└─────┬────┘    └──────────────────┘    └──────────────┘
      │
      ▼
┌──────────────────┐    ┌──────────────────┐
│ 节点教学循环       │───▶│ 艾宾浩斯调度      │
│ (正向讲解+反向验证)│    │ (复习队列管理)    │
└──────────────────┘    └──────────────────┘
```

---

## 8. 待定 / 后续讨论事项

- [ ] Agent prompt 的具体模板设计
- [ ] CLI 交互的具体命令设计（如 `learn`, `review`, `status`, `explore`）
- [ ] QA 打分系统的具体评分标准（LLM 打分 vs 关键词匹配 vs 混合）
- [ ] 概念指纹的标签体系是否需要预定义分类法
- [ ] 多用户支持的需求和范围
- [ ] 后续可能的 Web UI 迁移路径
- [ ] 图谱可视化方案
