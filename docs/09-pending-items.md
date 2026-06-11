# 09 — 待定事项与已决回填

> **更新**: 2026-06-11 | 随着 skill 化与分层重构落地，本文原先的「待定清单」中多数已被实现路径定型。下表把它们从「待定」回填为「已决」并指向实现位置；文末保留仍然开放的部分。

---

## 已决（回填，含实现指向）

| 原待定项 | 结论 | 实现指向 |
| -------- | ---- | -------- |
| Agent prompt 的具体模板设计 | 已落地：每个 agent 在自身模块内维护 system/user prompt 模板，按职责分工。 | `src/agents/`：`decomposer` / `assessor` / `examiner` / `teacher` / `reviewer` / `mnemonic` |
| QA 打分系统的具体评分标准（LLM vs 关键词 vs 混合） | **纯 LLM 打分**（非关键词、非混合）。`score_answer` 让模型输出 JSON `{score: 0.0–1.0, error_type(仅 score<0.6), explanation}`，按节点 `strictness_level` 调整严格度；错题本仅在 `score<0.6` 写入；pass 阈值取 `node.mastery_threshold` / `config.MASTERY_THRESHOLDS`。 | `src/agents/examiner.py`（`score_answer` / `_finalize_exam`）、`src/infrastructure/config.py:96`（`MASTERY_THRESHOLDS`） |
| 概念指纹是否需要预定义分类法 | **不预定义分类法**。`concept_fingerprint` 是拆解 agent 生成的 2–3 个自由文本抽象标签（如 `["隔离性","状态一致性"]`），存为 JSON list，用于跨域类比桥接；不强制统一 taxonomy。 | `src/agents/decomposer.py:57-76`、`src/data/database.py:64`（schema 注释） |
| 后续可能的 Web UI 迁移路径 | 已落地：`serve-learning-graph` 作为**纯数据平面**（React Flow + HeroUI），不调 LLM；生成交回 skill。 | docs/13、docs/14、docs/16；`skills/serve-learning-graph/web/` |
| 图谱可视化方案 | **双方案**：web 端交互式 React Flow 图（在线查看）+ `.drawio` 导出（离线/外部编辑）。 | `skills/serve-learning-graph/web/src/features/graph/GraphView.tsx`、`src/domain/drawio.py`（`export_goal_to_drawio`） |
| 搜索 API 的 provider 选型与配置方式 | **框架转变：不再自选/自配外部搜索 API**。拆解阶段的事实搜索委托给宿主 agent（Claude Code / Codex）自带的 web search，按需触发（仅时效 / 版本 / 标准 / 研究类话题或事实不确定会改变结构时）。因此无需 provider 选型与 key 配置。 | `skills/decompose-learning-goal/SKILL.md:18-19`；`config` 中无搜索相关 env |

---

## 仍待定 / 部分开放

- **多用户支持的范围**：基础隔离已具备——所有表都有 `user_id`（默认 `'default'`），CRUD 与 service 层均按 `user_id` 过滤（`src/data/database.py`、`src/services/*`）。**仍开放**：账户/认证体系、跨用户共享或协作、单机之外的多租户部署——这些超出当前单机 CLI + 本地 web 的范围，需要时另开设计文档。
- **概念指纹的跨域类比利用深度**：标签已生成并在大纲里用于类比桥接（`analogy_source_node`），但「按指纹在全图做跨域近邻匹配/推荐」尚未系统化，属后续可深化项（非阻塞）。

---

> 历史说明：本文最初是一份纯「待定」复选清单。随实现推进，多数项已由代码/skill 定型，故改为「已决回填 + 仍开放」两段式，避免设计文档与实现长期脱节。
