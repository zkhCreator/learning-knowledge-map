# 18 — 项目结构快照

> **版本**: v1.0 | **日期**: 2026-06-11 | **状态**: 现状梳理（非设计变更）
>
> 本文是对当前仓库（分支 `feature/migrate/skill`）的一次结构盘点：项目由哪些部分组成、各部分如何协作、迁移进行到哪一步，以及接下来建议怎么做。它**不引入任何新设计**，只记录现状；如需新增功能，仍走 CLAUDE.md 的「Discuss → Confirm → Implement」流程。

---

## 一句话定位

一个**递归学习图谱引擎**：把一个学习目标自动拆解成有依赖关系的原子知识点 DAG，再围绕这些节点提供初始评估、苏格拉底式学习、考试、错题本和艾宾浩斯间隔复习的全流程。

---

## 三个支柱

项目当前由三块组成，职责边界清晰：

| 支柱 | 位置 | 角色 | 是否调用 LLM |
| ---- | ---- | ---- | ------------ |
| **核心引擎** | `src/` | 分层 Python 包，承载所有业务逻辑、数据访问与 LLM agent | 是（在 agents/services 层） |
| **能力技能** | `skills/` | Claude Code / Codex skill，把核心能力包装成可被 agent 调用的工作流 | 经 `src` 间接调用 |
| **数据平面** | `skills/serve-learning-graph/web/` | 本地 React Flow GUI，只读写 SQLite、展示与作答 | **否**（纯数据平面，doc 16） |

三者共享同一份 SQLite 数据库（`data/learning.db`），这是它们之间唯一的耦合面。

```
┌─────────────┐   skill 调用    ┌──────────────────┐
│  skills/    │ ──────────────▶ │   src/ 核心引擎    │
│ (生成平面)   │  from src.*    │ (业务 + LLM agent) │
└─────────────┘                └────────┬─────────┘
       │ 深链(deep link)                  │ 读写
       ▼                                  ▼
┌──────────────────────────────────────────────────┐
│              data/learning.db (SQLite)            │
└──────────────────────────────────────────────────┘
       ▲ 只读写，永不调 LLM
       │
┌─────────────┐
│ web 数据平面 │  serve-learning-graph
└─────────────┘
```

---

## 一、核心引擎 `src/`（分层架构，详见 doc 17）

依赖严格单向向下，上层依赖下层，下层永不反向依赖：

```
interface ──▶ services ──▶ agents ──▶ domain ──▶ data ──▶ infrastructure
```

| 层 | 目录 | 关键文件 | 职责 |
| -- | ---- | -------- | ---- |
| 5 接口 | `src/interface/` | `main.py`（Typer CLI 全部命令）、`entrypoints.py`（启动 / 依赖缺失提示） | 终端展示，命令解析 |
| 4 服务 | `src/services/` | `assessment.py` `exam.py` `learning.py` `review.py` | 用例编排（无状态、面向 HTTP/skill） |
| 3 智能体 | `src/agents/` | `decomposer.py` `teacher.py` `examiner.py` `reviewer.py` `assessor.py` `mnemonic.py` | LLM 领域逻辑 |
| 2 领域 | `src/domain/` | `dag.py`（拓扑/路径/mastery 衰减）、`drawio.py`（导出） | 知识 DAG 结构与算法 |
| 1 数据 | `src/data/` | `database.py` | SQLite Schema + CRUD |
| 0 基础 | `src/infrastructure/` | `config.py` `logger.py` `llm.py`（原 `agents/client.py`，LLM 传输层）、`web_link.py`（深链 helper） | 配置、日志、LLM 传输、回链 |

> 每个文件都带 file-level doc 注释（Purpose / Responsibilities / What this file does NOT do），是理解各模块意图的首要依据。

---

## 二、能力技能 `skills/`（迁移的主战场）

每个 skill 是一个独立目录，含 `SKILL.md`（给 agent 的指令）、可选 `scripts/`（薄 CLI，`sys.path` 注入后 `from src.*` 调用核心）、`agents/`（子 agent prompt）、`references/`。

| Skill | 对应阶段 | 是否有 scripts |
| ----- | -------- | -------------- |
| `decompose-learning-goal` | 目标 → DAG 拆解 + 审查 + 持久化 | `persist_result.py` |
| `goal-assess` | 学习前初始评估，跳过已掌握节点 | — |
| `learn-start` | 单节点苏格拉底学习 | — |
| `exam-start` | 节点考试：出题 / 评分两模式 | `exam_cli.py`（generate / score） |
| `review-start` | 单次艾宾浩斯复习（复用考试流程） | — |
| `review-list` | 复习队列浏览（只读） | — |
| `print-learning-graph` | 终端打印父子知识树 | `print_graph.py` |
| `serve-learning-graph` | 启动本地 GUI 数据平面 | `workflow_api.py` `export_graph.py` + `server.js` |

**skill → src 的桥接方式**（以 `exam_cli.py` 为例）：脚本把仓库根加入 `sys.path`，再 `from src.services.exam import ...`、`from src.agents.examiner import ...`。即 skill 是核心引擎的**编排/呈现外壳**，不重复实现业务逻辑。

> `.claude/skills/` 是指向 `skills/` 的符号链接，让 Claude Code 能发现这些项目级 skill。

---

## 三、Web 数据平面（doc 16 的关键约束）

`skills/serve-learning-graph/web/` 是一个 React Flow + HeroUI 前端，覆盖 Graph / Assess / Learn / Exam / Review 五个 feature view（`web/src/features/*`）。**核心约束：永不调用 LLM**。

- 所有「生成类」动作（出题、评分、生成大纲/对话、再考）一律返回 `agent_required`，前端用 `AgentHandoffPanel` 展示「回工具继续」的命令与返回深链。
- 两层闸门：客户端边界 `infrastructure/llm.py`（`LDG_WEB_MODE` 下抛 `AgentCapabilityUnavailable`）+ 动作级早闸 `workflow_api.py`。
- serve 启动时把真实 URL 写到 `data/.web_url`（sidecar），生成 skill 读它拼深链把用户带回 web。

---

## 四、支撑目录

| 目录 / 文件 | 作用 |
| ----------- | ---- |
| `docs/` | 18 篇编号设计文档 + `summary.md` 索引；按 CLAUDE.md 规则 12 维护 |
| `tests/` | pytest，镜像 `src/` 结构，**384 个测试函数**，全程 mock LLM（规则 13 TDD） |
| `data/` | 运行时 SQLite（`learning.db`，gitignore）+ 运行日志 |
| `example/` | 演示数据：`seo-0-to-100k-dau.{json,md}` + 对应 db 快照 |
| `main.py` | CLI 标准入口（转发到 `src.interface.entrypoints.run_main`） |
| `.env` / `.env.example` | LLM 中转配置（`LLM_BASE_URL` / `LLM_API_KEY` / `DEFAULT_MODEL`） |
| `requirements.txt` | typer / rich / dotenv + LLM SDK |
| `CLAUDE.md` (`AGENTS.md` 软链) | 工作规则：file-level 注释优先、设计先行、TDD |

---

## 五、典型数据流（用户完整旅程）

```
1. 拆解   goal new / decompose-learning-goal  → 目标拆成原子节点 DAG（写 knowledge_nodes/edges）
2. 评估   goal-assess                          → 探测已知，写 user_knowledge_state，跳过已掌握
3. 学习   learn-start                          → 生成大纲 + 苏格拉底对话，追踪 coverage
4. 考试   exam-start generate → (web 作答) → exam-start score → 结算 mastery + 写错题本
5. 复习   review-list 看队列 → review-start    → 艾宾浩斯再考 → 更新 mastery + 排下次复习
       贯穿： mnemonic 助记策略层（doc 10）注入学习/复习
```

同一份数据，三种触达方式：**CLI**（`main.py`）、**skill**（agent 编排）、**web**（只读/作答）。

---

## 六、迁移现状（branch `feature/migrate/skill`，相对 main 21 个提交）

**已完成：**
- ✅ 八个 skill 全部落地（decompose / assess / learn / exam / review×2 / print / serve）
- ✅ GUI workflow host 五大 view 接通（doc 14/15）
- ✅ Web 收敛为纯数据平面，生成能力 handoff 回 skill（doc 16）
- ✅ `src/` 按依赖方向重排为分层包，重构前后 391 passed（doc 17）

- ✅ **CLI/Skill 边界已收口**（doc 19）：CLI 退化为纯数据平面，7 个生成命令从 Typer 删除，交给对应 skill；CLI 与 web 现在同为数据平面，「双轨」歧义消除（409 passed）。

**仍在演进 / 待收口：**
- ⚠️ doc 09 仍挂着的开放项：QA 评分标准、概念指纹标签体系、多用户支持、搜索 provider 选型等。

---

## 七、接下来的建议（按优先级）

### P0 — 收口迁移，消除双轨歧义
当前 `main.py` CLI 与 `skills/` 覆盖重叠能力，是最大的概念负担。建议**显式定义两者的目标分工**并写成一篇新设计文档（doc 19），例如：
- CLI 作为「无 agent 环境下的脚本化入口 / CI 友好接口」，skills 作为「agent 编排的主交互路径」；或
- CLI 逐步退化为 thin wrapper，只保留 `init` / `goal list` / `status` 等纯数据命令，生成类命令统一交给 skill。

先 **Discuss → Confirm**，再动代码。

### P1 — 给迁移补一道端到端回归 ✅（已完成）
已新增 `tests/test_cross_pillar_smoke.py`：在**一个真实 SQLite 文件**上走完 `decompose（persist_result）→ assess → learn → exam(generate / record / score) → review` 全链路（仅 mock LLM），逐段断言后一支柱能读到前一支柱写入的数据，并验证 web 数据平面（`workflow_api` 的 goals/graph/exam_get）读到生成平面写入的内容、且 `exam_get` 永不泄漏 `expected_answer`（doc 16）。这是双轨架构最容易悄悄回归的地方，现已被锁定。

### P2 — 把「现状结构」沉淀进 README 顶部
README 目前偏「CLI 使用手册」，没有体现 skills / web 数据平面这一层。建议在 README 顶部加一段「三支柱」导览 + 指向本文，降低新读者的认知门槛。

### P3 — 清理仓库噪声
根目录有 `__pycache__/`、`pytest-cache-files-ct3e5kfh/` 等未忽略产物，`data/.DS_Store` 等。建议核对 `.gitignore` 并清理已误入版本库的缓存目录，保持仓库整洁。

### P4 — 推进 doc 09 中已被实现路径触及的开放项
随着 skill 化推进，「QA 评分标准」（exam-start score 已落地具体评分）和「搜索 provider 选型」（decompose 已有 source-aware search）实际上已部分定型。建议回填 doc 09，把已定项从「待定」转为「已决」，避免设计文档与实现脱节。

---

## 关键文件速查

| 想了解 | 看这里 |
| ------ | ------ |
| 系统总览与理念 | `docs/01-system-overview.md` |
| 分层架构与旧→新路径映射 | `docs/17-layered-architecture.md` |
| Web 为何不调 LLM | `docs/16-web-data-plane-handoff.md` |
| 拆解的正向/反向 agent | `src/agents/decomposer.py` |
| DAG 算法与 mastery 衰减 | `src/domain/dag.py` |
| 全部 CLI 命令 | `src/interface/main.py` |
| SQLite Schema | `src/data/database.py` |
