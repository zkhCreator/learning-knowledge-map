# 19 — CLI / Skill 边界收口

> **版本**: v1.0 | **日期**: 2026-06-11 | **状态**: 已确认 / 实现中
>
> 本文定义迁移收口后 `main.py` CLI 与 `skills/` 的目标分工，消除 doc 18 指出的「双轨歧义」。
>
> **确认结论（2026-06-11）**：CLI 退化为纯数据平面；7 个生成命令**直接从 Typer 注册中删除**（非 handoff 桩）。敲旧命令会得到 Typer 的「No such command」。各保留命令里指向旧命令的提示文案改为指向对应 skill。

---

## 一句话目标

把 CLI 收敛为**纯数据平面**——和 web 数据平面（doc 16）同一原则：**CLI 永不调用 LLM**。所有触达 agent 的生成类命令交给对应 skill，CLI 命令只读写 SQLite、展示已生成内容。

## 设计原则

| 平面 | 载体 | 职责 | 调 LLM |
| ---- | ---- | ---- | ------ |
| 生成平面 | `skills/` | 拆解、评估、学习对话、出题/评分、复习再考 | 是 |
| 数据平面（CLI） | `main.py` | 初始化、查看图谱/状态/进度/结果、删除/导出 | **否** |
| 数据平面（web） | `serve-learning-graph` | 看图谱、作答、看结果 | 否 |

> 三个触达入口现在职责一致：**生成只在 skill，CLI 与 web 都是数据平面。** 这让「双轨」从「两套等价能力」变成「一套生成 + 两个数据视图」，歧义消除。

## 命令分类（已用代码核实是否触达 LLM）

### 保留在 CLI（纯数据，不调 LLM）

| 命令 | 说明 |
| ---- | ---- |
| `init` | 初始化数据库 |
| `goal list` | 列出目标 |
| `goal remove <id>` | 删除目标及关联数据 |
| `goal export <id>` | 导出 draw.io 图 |
| `goal tree <id>` | 打印知识树 |
| `goal nodes <id>` | 按学习顺序列原子节点 |
| `status` | 今日状态（待复习 + 下一节点） |
| `learn progress <node>` | 大纲覆盖进度（仅 `_print_sections_detail`，无 LLM） |
| `exam review <exam-id>` | 查看考试结果（读分数，无 LLM） |
| `errors list` | 错题本浏览（无 LLM） |
| `review list` | 复习队列（无 LLM） |

### 移交 skill（触达 LLM 生成）

| 命令 | 现调用 | 交给 |
| ---- | ------ | ---- |
| `goal new <title>` | `decomposer` | `/decompose-learning-goal` |
| `goal assess <id>` | `assessor.run_assessment_loop` | `/goal-assess` |
| `learn start <node>` | `teacher`（大纲+苏格拉底） | `/learn-start` |
| `learn chat <node>` | `teacher`（苏格拉底） | `/learn-start`（resume） |
| `exam start <node>` | `examiner`（出题） | `/exam-start generate` |
| `errors review <node>` | `examiner.score_answer` | `/exam-start score` / `/review-start` |
| `review start` | 再考（复用 exam） | `/review-start` |

## 移交方式：直接删除（已确认）

7 个生成命令从 Typer 注册中**直接删除**（连同其函数体与 CLI 专属 import）。敲旧命令时 Typer 报「No such command 'new'」。

> 注：备选的「handoff 桩」（命令保留、打印引导文案）曾被考虑以保可发现性，但最终选择直接删除以保持 CLI 干净。可发现性改由「文档 + 保留命令里的提示文案指向 skill」承接。

## 实现步骤（TDD）

1. **测试先行**（规则 13）：新增 `tests/test_cli_boundary.py`，断言 (a) 7 个生成命令不再注册（`goal new` / `goal assess` / `learn start` / `learn chat` / `exam start` / `errors review` / `review start` 调用返回非 0 且含 "No such command"），(b) 11 个数据命令仍可被发现（`--help` 退出码 0）。
2. 改 `src/interface/main.py`：删除 7 个生成命令函数 + 其 CLI 专属 import；保留 `learn` / `exam` / `review` 子 app（各仍有 ≥1 个数据命令）。
3. 把保留命令里指向旧命令的提示文案改为指向 skill：`goal list` / `goal nodes`（→ `/decompose-learning-goal`）、`status`（→ `/learn-start`、`/review-start`、`/decompose-learning-goal`）、`errors list`（→ `/review-start`）、`review list`（→ `/review-start`）。
4. 同步更新 `main.py` 顶部 docstring、`src/interface/main.py` 的 Available commands 注释、`README.md` 命令总览（删掉已移除命令，标注走 skill）。
5. `pytest` 全绿（规则 13）后收口。

## 非目标

- 不改 SQLite Schema、不改任何保留命令的行为
- 不删除 `src/agents/*`（skill 仍经 src 调用它们，agent 逻辑不动）
- 不改 skills 本身的实现（只是让 CLI 指向它们）
- 不引入新的 CLI 框架或 package 打包
