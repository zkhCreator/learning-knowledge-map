# 12 — Print Learning Graph Skill

> **版本**: v0.1 | **日期**: 2026-06-09 | **状态**: 设计确认

---

## 一句话描述

把 SQLite 中已持久化的学习图谱按父子节点关系打印成文本树，作为最轻量的图谱查看 skill。

## 目标

- 从现有 `learning_goals`、`knowledge_nodes`、`knowledge_edges` 和 `user_knowledge_state` 读取数据。
- 以 `knowledge_nodes.parent_node` 作为主树结构，保留中间节点和原子节点。
- 用 `knowledge_edges` 只补充 prerequisite / analogy 依赖信息，不把依赖边误当成父子树。
- 支持 `--db` 指定 SQLite 文件，`--goal` 指定完整 goal ID 或前缀，`--user` 限定用户。
- 不写数据库，不调用 LLM，不依赖主 CLI 的 Typer/Rich 运行环境。

## Skill 位置

```text
skills/print-learning-graph/
```

## 输入与输出

输入：

- SQLite DB 路径；默认优先使用 `DB_PATH`，否则使用当前工作目录 `learning.db`（docs/20）。
- Goal ID 或前缀；如果省略且当前用户只有一个目标，可自动选择该目标。
- User ID；默认 `default`。

输出：

- 控制台文本树。
- 每个节点展示掌握状态、原子/非原子标记、预估时间和必要的依赖摘要。
- 缺失 DB、目标不存在、目标前缀冲突时输出明确错误并返回非零退出码。

## Non-Goals

- 不创建或修改学习目标。
- 不启动 Web 服务。
- 不重新计算拆解结果。
- 不改变现有 CLI 命令或数据库 schema。

## 测试策略

- 使用 `example/learning.db` 验证真实导入结果可打印。
- 覆盖 goal 前缀解析、缺失目标、父子层级、节点数量和关键标题输出。
- 使用临时 SQLite 覆盖空库和冲突前缀等边界场景。
