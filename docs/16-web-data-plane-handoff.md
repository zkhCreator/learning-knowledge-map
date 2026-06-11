# 16 — Web 数据平面 + 生成 Handoff

> **版本**: v0.1 | **日期**: 2026-06-10 | **状态**: 设计确认 / 已实现

---

## 一句话描述

把 GUI workflow host 收敛为**纯数据平面**:它读写 SQLite、展示已生成内容、记录作答,但**永不调用大模型**;所有生成能力(评估探针、学习大纲/对话、考试出题/评分、复习再考)交回 Codex / Claude Code 的 skill 完成,完成后用深链把用户带回 web。

## 背景

此前 web GUI 在浏览器请求里直接调用 LLM agent。当 `anthropic`/`openai` 包缺失或没配 key 时,`src/agents/client.py` 抛出的 `ModuleNotFoundError("anthropic package is not installed…")` 会一路透传到前端,显示成对用户无意义的"没装包"。

目标:web 只做"固化内容"(查看、作答、保存);凡是会触达 agent 的地方,改成统一的 `agent_required` 提示,引导用户回工具继续,而不是报错。

## 两个平面

| 平面 | 载体 | 职责 |
| ---- | ---- | ---- |
| 生成平面 | Codex / Claude Code 里的 skill | 出题、评分、生成大纲/对话、复习再考。持久化到 SQLite,完成后打印回 web 的深链。 |
| 数据平面 | web GUI(`serve-learning-graph`) | 看图谱、加载已生成考卷、作答并保存、看结果。永不调用 LLM。 |

## 关键设计决策

1. **永远交给工具**:web 设计上就是纯数据层,即使配了 key 也不在 web 跑 LLM。所有生成类动作一律返回 `agent_required`。
2. **serve 启动时写 sidecar 文件**:serve 把实时 URL(含真实端口)写到 `data/.web_url`;生成 skill 读它拼深链(回退 `LDG_WEB_URL`,再回退 `http://localhost:8765`)。

## 闸门(两层)

- **客户端边界**(`src/agents/client.py`):当 `config.WEB_MODE`(env `LDG_WEB_MODE`)为真时,`call()` / `call_json()` 在导入/连接任何 SDK 之前抛 `AgentCapabilityUnavailable(code="agent_required")`。Node 服务在 `spawnSync` 时注入 `LDG_WEB_MODE=1`,使所有 web 触发的 Python 子进程都处于此模式。
- **动作级早闸**(`workflow_api.py`):生成类动作在产生任何 DB 副作用之前直接返回 `agent_required`,并附带 `skill` / `command` / `deep_link` 元数据(经 `_agent_required` 与扩展后的 `_error`/`WorkflowApiError.extra` 透传)。

## 动作分类

| 动作 | 平面 | 说明 |
| ---- | ---- | ---- |
| health / goals / graph | 数据 | 保留 |
| review_queue / review_start | 数据 | 队列 + 上下文(读缓存助记锚点,非 LLM) |
| exam_get(新) | 数据 | 加载工具已生成考卷,**不回传 expected_answer** |
| exam_record_answer(新) | 数据 | 持久化原始作答,`score=None`,不评分 |
| assessment_start / assessment_answer | handoff | → `/goal-assess` |
| learn_prepare / learn_message | handoff | → `/learn-start` |
| exam_start | handoff | → `/exam-start generate` |
| exam_answer / exam_finish | handoff | 评分/结算 → `/exam-start score` |
| review_finish | handoff | 再考结算 → `/review-start` |

## 考试端到端

1. 工具:`python skills/exam-start/scripts/exam_cli.py generate --node <node>` → 出题、持久化、打印深链 `…/?view=exam&exam=<id>`。
2. web:打开深链 → `exam_get` 展示题目 → 逐题作答 → `exam_record_answer` 仅存原始答案。
3. web:"提交评分(去工具)"→ `exam_finish` 返回 `agent_required`,显示 handoff 面板。
4. 工具:`exam_cli.py score --exam <id>` → 逐题评分 + `finish_exam` 结算 → 打印结果深链。
5. web:展示结果(读)。

## 前端

- `ApiError` 扩展可选 `skill` / `command` / `deep_link`。
- `AgentHandoffPanel`:`error.code === "agent_required"` 时渲染——说明、可复制命令、返回深链。
- `ApiErrorNotice`:统一分发,agent_required → 面板,其它 → 原行内 alert。各 feature view 把 `error` 状态从 `string` 改为 `ApiError`。
- `ExamView` 重构为"加载已生成考卷 + 记录原始作答 + 提交评分 handoff";深链 `?view=exam&exam=<id>` 直达。

## 关键文件

- `src/config.py`(`WEB_MODE`)、`src/agents/client.py`(`AgentCapabilityUnavailable` + 闸门)
- `skills/serve-learning-graph/scripts/workflow_api.py`(`_agent_required` + gate + `exam_get` / `exam_record_answer`)
- `skills/serve-learning-graph/scripts/server.js`(注入 `LDG_WEB_MODE`、写 sidecar、新路由)
- `src/services/exam.py`(`get_exam_view`、`record_answer`)、`src/web_link.py`(深链 helper)
- `skills/exam-start/scripts/exam_cli.py`(generate / score 模式)
- web:`types.ts`、`api.ts`、`components/AgentHandoffPanel.tsx`、`components/ApiErrorNotice.tsx`、各 `*View.tsx`

## Non-Goals

- 不在 web 里保留任何"key 在就直接跑 LLM"的 fallback。
- 不把评分/结算搬回 web(即使它本身不调用 LLM,也归属工具流程,以保证作答-评分边界清晰)。
- 不做 skill 之间的自动跳转。
