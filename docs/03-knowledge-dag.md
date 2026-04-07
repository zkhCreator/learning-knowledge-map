# 03 — 知识有向图（Knowledge DAG）

---

## 核心概念

- **节点**: 原子知识点，带有元信息（领域、难度、学习时间、QA 集、概念指纹）
- **前置依赖边**: A → B 表示学 B 之前必须掌握 A
- **跨域关联边**: 不同领域之间的类比/关联关系，附带类比说明

## SQLite Schema

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
    stability       REAL DEFAULT 1.0,
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

## 跨域关联：概念指纹机制

**原理**: 正向 Agent 在生成每个知识节点时，同时提取 2-3 个「抽象模式标签」作为概念指纹。

**示例**:
- "数据库事务" → `["隔离性", "原子操作", "状态一致性"]`
- "React useEffect" → `["副作用管理", "生命周期", "依赖追踪"]`
- "保险精算" → `["风险量化", "概率模型", "时间衰减"]`
- "分布式共识算法" → `["状态一致性", "容错", "多数派决策"]`

**关联发现**: 当用户跨领域学习时，系统对比新旧节点的概念指纹，发现共同的抽象模式（如"状态一致性"同时出现在数据库和分布式系统中），生成 `cross_domain_analogy` 边并附上类比说明。

**实现方式（现阶段）**: 不需要 embedding 向量检索，直接用 Agent 对比两个节点的 concept_fingerprint 即可。

## 跨目标图谱融合

用户学习多个目标后，各自的 DAG 逐渐融合成一张大图。之前学目标 A 时掌握的节点，可能直接成为目标 B 的前置，从而缩短新目标的学习路径。
