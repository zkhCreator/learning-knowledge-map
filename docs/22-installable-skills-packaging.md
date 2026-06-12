# 22 — 可独立安装的 Skill 打包（vendor + Codex）

## 目标

让 8 个 skill 能被 `npx skills add <owner>/<repo>`（vercel-labs/skills，GitHub
即 registry）独立安装到 Claude Code / Codex（`-a claude-code -a codex`），
脱离本仓库运行。安装器只复制各 skill 目录本身——skill 不能再依赖
`parents[3]` 上溯到仓库根 import `src/`。

## 方案：构建时原地 vendor，产物提交 git

`npx skills add` 直接读 GitHub 默认分支，所以 vendor 产物必须提交。

- vendor 目录：`skills/<skill>/_core/`，内含 `src/` 包完整副本 +
  `requirements.txt` + `.manifest.json`（源文件 sha256 清单）。
- `_core/` 没有 SKILL.md，不会被安装器误识别为独立 skill。
- 只有真正 import `src.*` 的 3 个 skill 需要 vendor：
  `decompose-learning-goal`（persist_result.py）、`exam-start`（exam_cli.py）、
  `serve-learning-graph`（workflow_api.py）。`print_graph.py` / `export_graph.py`
  是刻意的纯 stdlib 实现（文件级 Non-Goal），无需 vendor。
- web 前端构建产物 `web/dist/` 本就提交在 serve-learning-graph 内，随包分发。

## 运行时解析（_bootstrap.py）

每个有脚本的 skill 带 `scripts/_bootstrap.py`，按序探测并注入 sys.path：

1. `<skill>/_core/`（安装模式：vendored 运行时优先）
2. `<repo 根>`（开发模式回退：脚本上溯 3 级）

判据：候选目录下存在 `src/__init__.py`。脚本将自身目录插入 `sys.path` 后
`import _bootstrap`，替换原 `parents[3]` 注入块。

## 构建与防漂移

- `scripts/build_skills.py`：把 `src/`（剔除 `__pycache__`）与
  `requirements.txt` 复制进 3 个 skill 的 `_core/`，写 `.manifest.json`。
- `scripts/build_skills.py --check`：校验 vendored 副本与当前 `src/` 完全
  一致，不一致则非零退出。该检查作为 `tests/test_skill_packaging.py` 的
  一部分进 pytest——忘了重新 vendor 就提交会直接红。
- 改动 `src/` 后的流程：改代码 → `python scripts/build_skills.py` → 提交
  源与 vendor。

## 跨 skill 引用

goal-assess / learn-start / review-list / review-start 四个无脚本 skill 通过
serve-learning-graph 的 server.js 工作。SKILL.md 中的调用路径写为**相对自身
skill 目录的兄弟路径**（安装后各 skill 互为兄弟目录；仓库内开发时
`skills/` 下同样成立），并声明前置依赖：需一并安装 `serve-learning-graph`。

## 安装侧依赖（SKILL.md Requirements 小节）

- Python ≥ 3.10。纯数据步骤（persist / print / export / serve 数据面）仅
  stdlib；LLM 步骤需 `pip install anthropic openai rich python-dotenv`
  （`src/infrastructure/llm.py` 对缺包已 try/except 降级为 agent_required）。
- serve-learning-graph 需 Node ≥ 18（web/dist 已预构建，无需 pnpm）。
- WebSearch 为可选增强（按 `SEARCH_API_KEY` 探测，缺省自动跳过）。
- API key 通过环境变量提供（`LLM_API_KEY` / `ANTHROPIC_API_KEY` /
  `OPENAI_API_KEY`）；安装目录中不依赖 `.env` 文件（config.py 会读 CWD 的
  `.env`，开发仓库根的 `.env` 仅开发模式生效）。

## Codex 支持

每个 skill 的 `agents/openai.yaml` 提供 Codex subagent prompt；SKILL.md 不
假设 Claude 专有工具（子 agent 审查按"当前工具可用的 subagent 能力"措辞，
不可用时按 ReviewStatus.SKIPPED 降级——既有约定）。

## 验证

- `pytest tests/test_skill_packaging.py`：--check 同步校验、临时目录模拟
  安装（仅复制单个 skill 目录，子进程 import vendored src）、删除 `_core`
  后仓库模式回退仍可用。
- 真机：push 后 `npx skills@latest add <owner>/<repo> -a claude-code -a codex`。
