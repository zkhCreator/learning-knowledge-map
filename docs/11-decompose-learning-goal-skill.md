# 11 — Decompose Learning Goal Skill

> **版本**: v0.2 | **日期**: 2026-06-09 | **状态**: 设计确认 / Skill + SQLite persistence 实现

---

## 一句话描述

把当前项目里的递归目标拆解能力抽成独立 Skill：输入学习目标，按当前宿主工具可用能力执行 subagent 校验，输出学习 DAG 计划，同时把最终 JSON 通过确定性脚本保存到 SQLite，供现有 CLI 索引和读取。

## 目标

- 将“正向拆解 + 反向审查 + 递归原子化”的方法沉淀为可复用 agent workflow。
- 使用当前 Codex/大模型能力处理结构化 parse，而不是复制项目内 `client.py` 的 JSON 提取逻辑。
- 有 subagent 能力时做独立校验：Codex 环境使用 Codex subagent，Claude Code 环境使用 Claude Code Task/subagent。
- 无 subagent 能力时不阻塞输出，但必须在 JSON 中使用枚举值标记
  `review.status = ReviewStatus.SKIPPED ("skipped")`、
  `review.provider = ReviewProvider.NONE ("none")`。
- 对时效性、标准、版本、事实不确定内容按需搜索，并在输出中保留来源。
- 最终 JSON 必须通过 skill 自带 persistence 脚本落入 SQLite；模型不直接生成 SQL。
- SQLite 主表结构参考并复用当前 CLI 的 `src.db.database.SCHEMA_SQL`，保证 `goal tree`、`goal nodes`、后续 `learn/exam/review` 能读取。

## Skill 位置

首版放在项目内：

```text
skills/decompose-learning-goal/
```

后续通过软链或安装命令暴露给 Codex；在 Claude Code 等其他宿主工具中，则按对应工具的 skill/agent 加载方式使用同一套说明：

```text
~/.codex/skills/decompose-learning-goal -> <repo>/skills/decompose-learning-goal
```

## 输出形态

Skill 每次完成后必须输出两部分：

- 人类可读拆解：目标树、学习顺序、关键依赖、风险和待确认项。
- 机器可读 JSON：兼容当前项目的知识节点、依赖边、review 结果和来源记录。
- persistence 汇报：`db_path`、`goal_id`、`import_id`、导入节点数和边数。

JSON 字段以 `skills/decompose-learning-goal/references/output-schema.md` 为准。

## Persistence 设计

确定性脚本位置：

```text
skills/decompose-learning-goal/scripts/persist_result.py
```

DB 路径规则：

- 用户没有指定 DB 位置：在当前执行项目根目录写入 `learning.db`。
- 用户指定目录：在该目录下写入 `learning.db`。
- 用户指定 `.db` / `.sqlite` / `.sqlite3` 文件：使用该文件。
- 当前执行项目根目录优先按最近的 `.git` 目录判断；找不到则使用当前工作目录。
- 当前 CLI 默认读取 `src.config.DB_PATH`。如果需要让 CLI 读取 skill 写入的非默认 DB，运行 CLI 时设置 `DB_PATH=<db_path>`，或在 persistence 时显式使用 `--db data/learning.db`。

schema 策略：

- 主图表使用现有 CLI schema：`learning_goals`、`knowledge_nodes`、`knowledge_edges` 等由 `src.db.database.SCHEMA_SQL` 初始化。
- skill 独有信息进入 adjunct 表：`skill_result_imports`、`skill_result_sources`。
- 这些 adjunct 表保存 assumptions、sources、review、unresolved questions、raw JSON 和 content hash，不污染 CLI 主图表。
- importer 负责建库、建表、建索引、title 引用校验、JSON 字段映射和写入。
- 同一份 canonical JSON 用 `(user_id, content_hash)` 幂等导入；同一用户重复导入不会重复创建 goal/nodes/edges，不同用户会各自创建 CLI 可见目标。
- adjunct 表外键使用 cascade，避免 `goal remove` 删除 CLI 目标时被 metadata 阻塞。
- importer 会拒绝非法 `edge_type`、非原子节点参与的 prerequisite edge，以及 prerequisite cycle。

JSON 到 CLI 数据模型的关键映射：

- `target` -> `learning_goals.title`
- 唯一 `parent_title = null` 的节点 -> `learning_goals.root_node`
- `nodes[]` -> `knowledge_nodes`
- `parent_title` -> `knowledge_nodes.parent_node`
- `qa_draft[]` -> `knowledge_nodes.qa_set[]`
- `edges[].from_title/to_title` -> `knowledge_edges.from_node/to_node`

## 编排流程

1. 明确学习目标、用户已知领域、约束和输出用途。
2. 判断是否需要网络搜索；稳定基础知识默认不搜索。
3. 生成初版节点、依赖边、概念指纹、严格度、QA 草案。
4. 如果当前环境有 subagent 能力，启动 reviewer 做两层 review：每个父节点拆出的一组 children 做 local batch review，最终完整 DAG 做 global review。
5. 不做逐节点孤立 review；质量判断以“父节点 -> 子节点组”和“全图 DAG”为单位。
6. 如果当前环境没有 subagent 能力，跳过独立 review，并在最终 JSON 中使用 `ReviewStatus.SKIPPED` 标记。
7. 根据 review 反馈修改；无法确认的问题进入 `unresolved_questions`。
8. 输出人类可读说明和 JSON。
9. 调用 `scripts/persist_result.py` 保存最终 JSON，并汇报 DB 路径和导入 ID。

## Non-Goals

- 不让模型自由生成 SQL 或临场决定数据库结构。
- 不改变当前 CLI 主图表语义；persistence 主表必须对齐 CLI schema。
- 不把 WebSearch 固定为 Google CSE；搜索能力由当前 Codex 环境提供。
- 不写本地 parse/search 脚本；首版优先使用模型结构化输出能力。
- 不在没有 subagent 的情况下伪装成已审查；必须用 review enum 显式标注 skipped。
- `agents/openai.yaml` 仅作为 Codex UI metadata，不承担 reviewer agent 配置；reviewer 行为由 `SKILL.md` 和 `references/review-checklist.md` 定义。
