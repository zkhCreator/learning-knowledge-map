# 14 — GUI Workflow Host

> **版本**: v0.2 | **日期**: 2026-06-10 | **状态**: 设计确认 / 实施中（v0.2: 采用真实 HeroUI）

---

## 一句话描述

将 `serve-learning-graph` 从只读图谱查看器升级为本地 GUI 工作流控制台，后续所有 Web 行为都在同一个 React/Vite 项目上迭代。

## 背景

`docs/13-serve-learning-graph-skill.md` 定义的 v0.2 只负责图谱读取和 React Flow 展示，明确不写数据库。新的学习/复习目标需要用户在浏览器里执行评估、学习、考试和复习，这些行为会写入 SQLite，因此需要一个新的设计边界。

本文件是 v0.3 GUI host 的设计来源。v0.2 的只读图谱规则仍适用于 Graph 视图；工作流视图允许通过受控 service adapter 写数据库。

## 目标

- `skills/serve-learning-graph/` 成为唯一 Web GUI skill。
- Graph、Assess、Learn、Exam、Review 共享同一套视觉风格、导航和 API client。
- 每条学习/复习路径都是独立入口，不依赖另一个 UI flow 的隐式跳转。
- Node server 继续使用 Node 内置 `http`，不引入 Express，不引入 Node SQLite binding。
- Python 负责读取/写入 SQLite 和调用现有 `src.agents.*` 业务模块。
- 前端统一使用 **真实 HeroUI**（`@heroui/react` + Tailwind）作为组件与主题层（见下方 “视觉风格” 的 v0.2 决策）。

## Non-Goals

- 不把 `export_graph.py` 改成通用工作流 API。
- 不在 Node 进程中直接访问 SQLite。
- 不把 CLI 的 blocking REPL (`run_*_loop`) 暴露给 HTTP。
- 不在 server 启动时安装依赖或动态构建前端。
- 不开放远程访问或多用户认证；首版只绑定本机地址。
- 不在同一个 commit 中混合多个 workflow skill 的实现。

## 设计边界

### GUI Host

`skills/serve-learning-graph/scripts/server.js` 负责：

- 托管 `web/dist`。
- 提供 `/graph.json` 的兼容入口。
- 提供 `/api/*` 本地 HTTP API。
- 对每个 API 请求调用 Python service adapter 子进程。
- 将 JSON 请求体传给 Python，并把 JSON 响应返回浏览器。
- 保持前台进程模式，端口占用时继续自动递增。

`server.js` 不负责：

- 解析 SQLite。
- 保存学习状态。
- 调用 LLM。
- 长时间持有数据库事务。

### Python Service Adapter

新增 `skills/serve-learning-graph/scripts/workflow_api.py`，作为 GUI host 的唯一 Python API 入口。

职责：

- 在导入 `src.config` 之前处理 `--db`，确保 `DB_PATH` 不被提前固定。
- 解析 action 名称和 JSON payload。
- 调用 workflow service 函数。
- 返回稳定 JSON。
- 将错误转成 `{ok: false, error: {code, message}}`。

后续各 skill 可以复用同一 service 层，但 skill wrapper 仍放在各自目录，保持用户入口独立。

### Workflow Service 层

新增 `src/services/learning_workflows.py` 或按模块拆分 `src/services/*`，承接非交互业务流程。

职责：

- 复用 `src.agents.assessor`、`teacher`、`examiner`、`reviewer` 的纯函数能力。
- 避免调用 `input()` 和 Rich console 输出。
- 为 GUI 和 skill script 提供相同的函数边界。
- 在单元测试中 mock `llm.call` / `llm.call_json`。

不修改 `src/cli/main.py` 的职责；CLI 仍是终端展示层。

## API 设计

所有写接口都必须显式传入 `user_id` 和目标资源 ID，避免隐式全局路径。

### Read APIs

- `GET /api/health`
- `GET /api/goals?user=default`
- `GET /api/graph?goal=<goal-id-or-prefix>&user=default`
- `GET /api/status?user=default`
- `GET /api/nodes/<node-id-or-prefix>?user=default`
- `GET /api/learn/<node-id-or-prefix>/outline?user=default`
- `GET /api/learn/<node-id-or-prefix>/session?user=default`
- `GET /api/exams/<exam-id-or-prefix>?user=default`
- `GET /api/errors?user=default&node=<node-id-or-prefix>`
- `GET /api/review/queue?user=default&include_future=false`

### Write APIs

- `POST /api/goals/<goal-id-or-prefix>/assessment/start`
- `POST /api/goals/<goal-id-or-prefix>/assessment/answer`
- `POST /api/learn/<node-id-or-prefix>/prepare`
- `POST /api/learn/sessions/<session-id>/messages`
- `POST /api/exams/start`
- `POST /api/exams/<exam-id>/questions/<question-id>/answer`
- `POST /api/exams/<exam-id>/finish`
- `POST /api/review/start`
- `POST /api/review/<review-id>/finish`

## 独立路径

### Goal Assess

入口：`/assess?goal=<goal-id>`

路径：

1. 用户选择目标和 self-report level。
2. API 返回第一个 probe question。
3. 用户提交答案。
4. API 评分并写入 `user_knowledge_state`。
5. API 返回下一个 probe 或 assessment summary。

不自动进入 Learn，也不自动创建 Review。用户可从 summary 选择下一步。

### Learn Start

入口：`/learn?node=<node-id>`

路径：

1. API 生成或复用 outline。
2. API 创建或恢复 learning session。
3. 用户逐轮发送 message。
4. API 调用 Socratic turn，写入 chat history 和 progress。
5. 达到考试条件时只显示 Exam 入口，不自动启动考试。

### Exam Start

入口：`/exam?node=<node-id>`

路径：

1. API 创建 exam attempt 和 questions。
2. 用户逐题提交答案。
3. API 评分并保存 question score。
4. API finalize exam，更新 state、error notebook 和 review schedule。

### Review List

入口：`/review`

路径：

1. API 读取 review queue。
2. 前端按 critical、overdue、today、future 分组展示。
3. 用户选择一个 review，再进入 Review Start。

### Review Start

入口：`/review/start?review=<review-id>` 或 `/review/start?node=<node-id>`

路径：

1. API 返回 review context：目标节点、历史错题、助记 retrieval context。
2. 用户确认开始复习考试。
3. API 复用 Exam 原语创建 review exam。
4. API 完成旧 review 并安排下一次 review。

Review 可以复用考试 API，但入口、上下文和完成逻辑必须独立于 normal Exam flow。

## 前端结构

```text
skills/serve-learning-graph/web/src/
  App.tsx                 # shell + route state
  api.ts                  # typed fetch client
  types.ts                # shared API contracts
  styles.css              # shared design tokens
  features/
    graph/
    assess/
    learn/
    exam/
    review/
  components/
    AppShell.tsx
    ModeTabs.tsx
    SummaryStrip.tsx
```

`flow.ts` 保持 graph transform 职责，不放 workflow 状态。

## 视觉风格

> **依赖决策 (v0.2, 2026-06-10)**: 经用户确认，前端采用**真实 HeroUI**（`@heroui/react` + Tailwind + `HeroUIProvider`），不再使用 v0.1 的 “仅 token 模拟、不引入依赖” 方案。HeroUI 主题 token 作为五个 flow 的统一视觉来源；`web/dist` 在引入后需重建并提交。新增依赖仅限 HeroUI 及其要求的 Tailwind/PostCSS 工具链，review gate 对 lockfile churn 的限制相应放宽到这一范围。

- 工具型工作台，不做 landing page。
- 左侧或顶部为紧凑导航：Graph、Assess、Learn、Exam、Review。
- 主区域使用全宽 workspace；卡片只用于独立项目、题目、队列项和详情面板。
- 控件使用熟悉语义：tabs、segmented controls、icon buttons、progress bars、textarea、forms。
- 字体不随 viewport 缩放，letter spacing 保持 0。
- 配色避免单一蓝紫主题：使用中性底色、蓝色主操作、绿色掌握、黄色待复习、红色风险、青色类比。
- 固定格式 UI 使用稳定尺寸，避免题目、按钮、节点 hover 导致布局跳动。

## 测试策略

实现顺序必须遵循 TDD：

1. 先写 service adapter 测试。
2. 再写 HTTP endpoint smoke tests。
3. 再写前端 type/build gate。
4. 最后做浏览器 smoke test。

必跑命令：

```bash
.venv/bin/python -m pytest -q
cd skills/serve-learning-graph/web && pnpm install   # 首次引入 HeroUI/Tailwind 后必跑
cd skills/serve-learning-graph/web && pnpm typecheck
cd skills/serve-learning-graph/web && pnpm build
```

涉及本地端口绑定的 pytest 在 sandbox 受限时需要提升权限运行。

## 提交策略

每个模块独立提交：

- `default-agent: document GUI workflow host design`
- `gui-agent: add workflow host foundation`
- `assessor-agent: add goal assess skill and GUI flow`
- `learn-agent: add learn start skill and GUI flow`
- `exam-agent: add exam start skill and GUI flow`
- `review-agent: add review list skill and GUI queue`
- `review-agent: add review start skill and GUI flow`
- `default-agent: complete global workflow review`

每个模块提交前必须完成：

- tests first
- implementation
- pytest/build gate
- review agent pass
- no unrelated file churn

