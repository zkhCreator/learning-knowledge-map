# 15 — Agent Burndown

> **版本**: v0.1 | **日期**: 2026-06-09 | **状态**: 设计确认 / 待执行

---

## 一句话描述

用前置燃尽图管理 GUI host 和五个 workflow skill 的开发顺序、依赖、review gate 和独立提交边界。

## 目标

- 每个 agent 只负责自己的模块边界。
- 每个模块完成后由 review agent 检查，再提交。
- 所有模块完成后由 default agent 做全局 review。
- 保证依赖顺序清晰，避免多个 agent 同时修改同一文件造成冲突。
- 保证每个 commit 都能单独解释、单独回滚、单独验证。

## Agent 分工

| Agent | 模块 | 主要写入范围 | 主要验证 |
| ---- | ---- | ---- | ---- |
| default-agent | 设计和全局 review | `docs/`, final integration notes | docs index、全局目标覆盖 |
| gui-agent | GUI host foundation | `skills/serve-learning-graph/scripts/`, `skills/serve-learning-graph/web/src/` | HTTP API smoke、typecheck、build |
| assessor-agent | goal assess | `skills/goal-assess/`, assessment service/API, assess web feature | mocked LLM tests、assessment state writes |
| learn-agent | learn start | `skills/learn-start/`, learning service/API, learn web feature | outline/session/chat tests |
| exam-agent | exam start | `skills/exam-start/`, exam service/API, exam web feature | question/answer/finalize tests |
| review-agent | review list/start | `skills/review-list/`, `skills/review-start/`, review service/API, review web feature | queue/context/reschedule tests |
| review-agent | module review | read-only review of each completed module | findings first, file/line references |

## 依赖顺序

```text
Design baseline
  -> GUI host foundation
      -> goal assess skill + GUI
      -> learn start skill + GUI
      -> exam start skill + GUI
      -> review list skill + GUI
          -> review start skill + GUI
              -> global review
```

原因：

- GUI host foundation 提供统一 service adapter、API response contract、Web shell 和 style tokens。
- Goal Assess 只依赖 graph/state 读取和 assessment state 写入，适合先落地。
- Learn Start 依赖 session/outline/chat 基础能力，不依赖 Exam。
- Exam Start 依赖 outline 和 examiner 原语，会创建 review schedule。
- Review List 依赖 exam 通过后产生的 review schedule。
- Review Start 复用 exam 原语，但入口和完成逻辑独立，因此必须在 exam foundation 后实现。

## 燃尽表

| 序号 | 模块 | 状态 | 退出条件 | Commit |
| ---- | ---- | ---- | ---- | ---- |
| 1 | 设计基线 | done | docs 14/15 完成，summary 同步 | `20a1819` / `643b627` (HeroUI v0.2) |
| 2 | GUI host foundation | done | API adapter + shell + graph compatibility tests/build 通过 | `82f41e8` |
| 3 | goal assess skill | done | skill artifact + service/API + GUI view + review pass | `f278192` |
| 4 | learn start skill | done | skill artifact + service/API + GUI view + review pass | `ae9e474` |
| 5 | exam start skill | done | skill artifact + service/API + GUI view + review pass | `8198e3a` |
| 6 | review list skill | done | skill artifact + service/API + GUI queue + review pass | `b508bfd` |
| 7 | review start skill | done | skill artifact + service/API + GUI flow + review pass | `f9caedf` |
| 8 | global review | done | objective checklist fully verified | `default-agent: complete global workflow review` |

> **执行记录 (2026-06-10)**: 全部 7 个实现模块按依赖顺序串行落地，每个模块通过独立的只读 review agent 后再提交。`.claude/skills/` 通过软链接暴露全部项目 skill（含 5 个新 workflow skill），使其可被 Claude 直接调用（commit `5a3504e`）。HeroUI v3 + Tailwind v4 作为统一组件层（commit `643b627`）。最终全量门禁：`pytest` 366 passed、`pnpm typecheck` + `pnpm build` 通过；exam→review 跨模块端到端冒烟验证通过（失败考试不排程、通过考试排程，单测覆盖通过路径）。Module 5 由 default agent 在原 exam-agent 触达会话上限后补完（service+tests 已就绪，补齐 adapter/routes/skill/web）。

## 每模块工作流

1. 读文件级注释，确认职责边界。
2. 写 pytest 或 frontend type-level tests。
3. 实现最小 service/API/script/UI。
4. 运行目标模块测试。
5. 运行全量 `pytest`。
6. 运行 `pnpm typecheck` 和 `pnpm build`。
7. 唤起 review agent 做只读 review。
8. 修复 review findings。
9. `git status --short` 检查只包含本模块文件。
10. 独立 commit。

## Review Agent 检查清单

Review agent 每次只读检查，按 severity 输出 findings：

- 是否遵守文件级注释的职责和 Non-Goals。
- 是否存在隐式跨 flow 跳转。
- 是否所有 API 都显式传入 `user_id` 和资源 ID。
- 是否有真实 API call 出现在 unit tests。
- 是否存在 `src.config` 在 `--db` 设置前被导入的问题。
- 是否有 Node 直接访问 SQLite。
- 是否有 CLI REPL 被 HTTP 调用。
- 是否遗漏 GUI 扩展。
- 是否遗漏 docs/summary 更新。
- 是否引入不必要依赖或 lockfile churn。

## 提交规则

- 每个模块一个 commit。
- commit message 以 agent 名称开头。
- 不把 review 修复拆到另一个模块 commit。
- 不提交未验证的 dist 或缓存文件，除非该模块明确需要更新 committed build。
- 不修改无关文件。

## 全局完成审计

最终完成前 default agent 必须逐项验证：

- `serve-learning-graph` 已成为 GUI host。
- 所有 Web 行为都在同一个 `web/` 项目中扩展。
- 五个 workflow skill 目录存在且有 `SKILL.md` 和 `agents/openai.yaml`。
- 每个 skill 有对应 service/API 和 GUI view。
- 每个 flow 对用户而言有独立入口。
- 每个模块有 review agent 检查记录。
- 所有要求的测试和 build gate 通过。
- commit history 按模块切分。

