# 13 — Serve Learning Graph Skill

> **版本**: v0.2 | **日期**: 2026-06-09 | **状态**: 设计确认 / React Flow TypeScript 实现

---

## 一句话描述

把 SQLite 中的学习图谱导出为 JSON，并用前台 Node HTTP server 托管 React Flow TypeScript 页面。

## 目标

- 读取同一套 CLI SQLite 表，React Flow 主视图基于 `knowledge_nodes.parent_node` 的父子关系。
- 页面展示目标信息、节点图、原子节点数量、总预估时间、prerequisite 和 cross-domain analogy 关系。
- Node server 只使用 Node 内置模块；React/Vite/React Flow 依赖限定在 `web/` 前端项目中。
- 默认端口为 `8765`；端口占用时自动向上递增，直到找到可用端口。
- 服务以前台方式运行，用户用 `Ctrl+C` 停止。
- 启动后尽量打开本地页面；无法打开时打印 URL。

## Skill 位置

```text
skills/serve-learning-graph/
```

## 数据流

1. Python exporter 从 SQLite 读取 goal、nodes、edges、user state。
2. exporter 生成稳定 JSON：goal metadata、node hierarchy、flat nodes、typed dependency/analogy edges、summary。
3. Node server 启动时调用 exporter，缓存 JSON payload，并托管 `web/dist`。
4. React app 访问 `/graph.json`，在浏览器中转换为 React Flow nodes/edges。

## 输入与输出

输入：

- `--db`：SQLite DB 路径；默认同 print skill。
- `--goal`：完整 goal ID 或前缀；省略时允许单目标自动选择。
- `--user`：用户 ID；默认 `default`。
- `--port`：首选端口；默认 `8765`。

输出：

- 前台 Node server 进程。
- 控制台打印实际 URL、DB 路径、goal 标题和停止方式。
- 本地网页展示 React Flow 图谱、节点详情、prerequisite、analogy 和 dependents。

## Non-Goals

- 不引入 Express 或 SQLite Node binding。
- 不做数据库写入。
- 不做力导向布局；首版使用确定性树状自动布局。
- 不管理后台进程或持久 PID 文件。
- 不在 server 启动时安装依赖或动态构建前端。

## 测试策略

- 使用 `example/learning.db` 验证 exporter 输出真实 SEO 图谱。
- 使用临时端口占用测试端口递增逻辑。
- 用 Node smoke test 验证 `/`、`/assets/...`、`/graph.json` 和 404 响应。
- 用 TypeScript build 验证 React Flow app 类型正确并能生成 `web/dist`。
- 用 pytest 统一驱动 Python exporter 和 Node server 子进程测试。
