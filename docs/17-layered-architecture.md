# 17 — 分层架构

> **版本**: v1.0 | **日期**: 2026-06-11 | **状态**: 已落地

---

## 目的

把 `src/` 下原本散落的模块按**依赖方向**重排为清晰的分层包结构，让人打开 `src/` 一眼就能看出每个模块属于哪一层、依赖谁。本次只做物理位置与命名调整 + import 重写，**不改变任何运行逻辑**（重构前后 `pytest` 均为 391 passed）。

## 分层模型

依赖严格单向向下：上层可依赖下层，下层永不依赖上层。

| 层 | 目录 | 依赖 | 职责 |
| -- | ---- | ---- | ---- |
| **5 接口层 Interface** | `src/interface/` | services | Typer CLI、命令入口（`main.py` / `entrypoints.py`），仅做终端展示 |
| **4 服务层 Services** | `src/services/` | agents · data · domain | 用例编排：`assessment` / `exam` / `review` / `learning` |
| **3 智能体层 Agents** | `src/agents/` | infrastructure · data · domain | LLM 领域逻辑：`decomposer` / `teacher` / `examiner` / `reviewer` / `assessor` / `mnemonic` |
| **2 领域层 Domain** | `src/domain/` | infrastructure · data | 知识 DAG 结构与算法（`dag.py`）、drawio 导出（`drawio.py`） |
| **1 数据层 Data** | `src/data/` | infrastructure | SQLite 访问与 Schema（`database.py`） |
| **0 基础层 Infrastructure** | `src/infrastructure/` | — | 配置 `config`、日志 `logger`、LLM 传输 `llm`、回链 helper `web_link` |

```
interface ──▶ services ──▶ agents ──▶ domain ──▶ data ──▶ infrastructure
                                 └──────────────────────────────┘
              （agents/domain/data 都可直接依赖 infrastructure）
```

## 本次迁移：路径映射

| 旧路径 | 新路径 |
| ------ | ------ |
| `src/config.py` | `src/infrastructure/config.py` |
| `src/logger.py` | `src/infrastructure/logger.py` |
| `src/web_link.py` | `src/infrastructure/web_link.py` |
| `src/agents/client.py` | `src/infrastructure/llm.py` |
| `src/db/` | `src/data/` |
| `src/graph/` | `src/domain/` |
| `src/cli/` | `src/interface/` |
| `src/agents/` · `src/services/` | 不变 |

> 历史设计文档（如 [14](14-gui-workflow-host.md)、[16](16-web-data-plane-handoff.md)）中出现的 `src/agents/client.py`、`src/cli/main.py`、`src/db/` 等路径，按上表对应到新结构；这些文档记录的是当时迭代的状态，不再回改。

## 关键设计决策

- **`client.py` → `infrastructure/llm.py`**：它是 LLM 传输层（provider 适配、JSON 解析、`AgentCapabilityUnavailable` 闸门），属于基础设施，而非某个领域 agent，因此移出 `agents/` 单独成层。
- **`graph/` → `domain/`**：DAG 是业务领域模型，用「领域层」命名比框架色彩的「graph」更能表达其地位。
- **`cli/` → `interface/`**：为将来可能的其它入口（Web 数据平面、API）预留语义空间——接口层不等于 CLI。
- **保留 `src` 顶包名**：不引入 `setup.py`/package 改造，导入仍是 `from src.<layer>...`，降低本次改动面。

## 非目标

- 不调整任何函数/类的职责或签名
- 不改变 SQLite Schema、CLI 命令、agent prompt
- 不引入新的依赖注入框架或 package 打包机制
