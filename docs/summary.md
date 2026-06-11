# 设计文档索引

> 递归学习图谱引擎 — 系统设计文档集

| 编号 | 文件 | 说明 |
| ---- | ---- | ---- |
| 01 | [system-overview](01-system-overview.md) | 系统概览、设计理念、技术栈、整体数据流 |
| 02 | [plan-decomposer](02-plan-decomposer.md) | 递归目标拆解器：正向 Agent 拆解 + 反向 Agent 审查 |
| 03 | [knowledge-dag](03-knowledge-dag.md) | 知识 DAG 结构、SQLite Schema、概念指纹与跨域关联 |
| 04 | [learning-path](04-learning-path.md) | 学习路径算法：初始评估、最短路径、动态调整 |
| 05 | [ebbinghaus-scheduler](05-ebbinghaus-scheduler.md) | 艾宾浩斯复习调度：间隔模型、mastery 衰减、严格度分级 |
| 06 | [node-learning-flow](06-node-learning-flow.md) | 节点级学习流程：大纲生成、苏格拉底对话、考试、错题本 |
| 07 | [data-tables](07-data-tables.md) | 新增数据表：学习会话、对话、考试、错题本 |
| 08 | [cli-commands](08-cli-commands.md) | CLI 命令设计：已实现 + 待实现命令 |
| 09 | [pending-items](09-pending-items.md) | 待定事项与已决回填：多数项已由实现定型（评分/指纹/搜索/可视化/Web），含实现指向 + 仍开放项 |
| 10 | [mnemonic-strategy-layer](10-mnemonic-strategy-layer.md) | 助记策略层：认知偏好模型、记忆宫殿/逻辑链/叙事编码、与学习复习流程的集成 |
| 11 | [decompose-learning-goal-skill](11-decompose-learning-goal-skill.md) | 将目标拆解能力抽成独立 agent skill：递归拆解、按需搜索、subagent 校验、双格式输出、SQLite persistence |
| 12 | [print-learning-graph-skill](12-print-learning-graph-skill.md) | 将 SQLite 中的学习图谱按父子节点关系打印为文本树 |
| 13 | [serve-learning-graph-skill](13-serve-learning-graph-skill.md) | 用前台 Node server 将 SQLite 学习图谱渲染为本地可交互页面 |
| 14 | [gui-workflow-host](14-gui-workflow-host.md) | 将 serve-learning-graph 升级为统一 GUI 工作流控制台，承载 Graph、Assess、Learn、Exam、Review |
| 15 | [agent-burndown](15-agent-burndown.md) | 前置 agent 燃尽图：模块分工、依赖顺序、review gate 与独立提交边界 |
| 16 | [web-data-plane-handoff](16-web-data-plane-handoff.md) | Web 收敛为纯数据平面：生成能力交回 Codex/Claude Code，agent_required handoff、sidecar 深链、考试作答/评分拆分 |
| 17 | [layered-architecture](17-layered-architecture.md) | 按依赖方向把 src/ 重排为分层包：infrastructure/data/domain/agents/services/interface，含旧→新路径映射 |
| 18 | [project-structure](18-project-structure.md) | 项目结构快照：三支柱（核心引擎/技能/数据平面）、典型数据流、迁移现状与下一步建议 |
| 19 | [cli-skill-boundary](19-cli-skill-boundary.md) | CLI/Skill 边界收口：CLI 退化为纯数据平面，7 个生成命令以 handoff 桩交给 skill |
