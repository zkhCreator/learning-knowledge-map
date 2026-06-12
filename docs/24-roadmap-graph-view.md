# 24 — Roadmap 式学习路线图视图

## 目标

把 Graph 视图从"全量左右树"重构为 roadmap.sh 式学习路线图：一眼看清学习
主线，层级可收起展开，节点状态/进度直观可读。保持既有 Non-Goals：纯数据
平面（只读、不调 LLM）、flow.ts 只算布局不取数、手写确定性布局（不引入
dagre/elk —— doc 13 的刻意决策）。

## 布局（flow.ts::buildRoadmapGraph）

- **主干**：根节点在顶部，其直接子节点（"主干步骤"）按导出顺序自上而下
  排成居中一列（x=0）。
- **侧枝**：每个主干步骤的子树作为 side branch 左右交替水平展开（奇数步
  向右、偶数步向左），枝内沿用既有 leaf-counting 思想：深度沿 ±x 推进，
  兄弟沿 y 堆叠，父节点取子节点 y 均值。
- **折叠**：`buildRoadmapGraph(payload, collapsed: Set<string>)` 纯函数；
  `collapsed` 内主干步骤的整个子树输出 `hidden: true`（React Flow v12
  原生支持），关联边端点任一隐藏则边隐藏。布局只为可见叶分配纵向空间，
  折叠即收紧。首版折叠仅作用于主干步骤的直接子树（更深层随枝整体显隐）。
- **默认态**：全部有子树的主干步骤折叠——首屏只看到学习主线。
- 多树（unattached_trees）依次向下顺延，复用 FOREST_GAP。
- 经典视图 `buildFlowGraph` 保留（print/调试用途），Graph 视图默认走
  roadmap。

## 节点视觉

- 状态色（与 user_knowledge_state.status 一致，doc 21 B7 落地后含
  needs_review）：unknown 灰 / learning 蓝 / needs_review 琥珀 /
  mastered 绿 + ✓。
- **进度环**：SVG 圆环展示 effective_mastery（时间衰减后的掌握度）。
- 折叠主干步骤显示 ▸ 与子孙计数徽章，展开显示 ▾。

## mastery.ts（与 dag.py:33-56 对应）

TS 复刻 `effective_mastery(raw, stability, last_reviewed) =
raw × exp(-days/max(stability, 0.1))`；未复习过或 raw=0 → 0；时间解析失败
→ raw。KnowledgeState 已含全部输入字段，**无需改 export_graph 协议**。

跨实现互验样本（写死进两端测试）：

| raw | stability | days | expected |
| --- | --------- | ---- | -------- |
| 0.9 | 2.0 | 1 | 0.9·e^(−0.5) ≈ 0.54588 |
| 0.9 | 2.0 | 0 | ≈ 0.9 |
| 0.8 | 0.05（→0.1 下限） | 1 | 0.8·e^(−10) ≈ 3.63e−5 |
| any | any | never reviewed | 0 |

## 详情侧栏（DetailPanel）

新增：状态徽章 + 有效掌握度百分比 + 三个入口链接（学习 / 考试 / 复习），
即既有 deep-link 约定 `?view=learn&node=…` / `?view=exam&node=…` /
`?view=review&node=…` 的普通锚链接——GraphView 不持有跨流路由状态，仅产出
URL（文件级注释同步更新）。

## 考前回忆卡（doc 23 4b 的 GUI 端）

`types.ts` 的 `ExamStartData` / `ExamViewData` 增加可选
`mnemonic: ReviewMnemonic | null`；ExamView 在题目上方渲染 `<details>`
折叠卡"🧠 考前回忆"，先回忆（prompt）再展开锚点参考（display）。

## 前端测试

引入最小 vitest（仅纯函数，组件层继续靠 tsc + Python 端 API 测试）：

- `flow.test.ts`：roadmap 布局确定性（同输入同坐标）、主干居中、折叠
  hidden 标记正确、折叠收紧纵向空间。
- `mastery.test.ts`：上表样本互验。

`package.json` 增加 `"test": "vitest run"`。验证：`pnpm typecheck && pnpm
test && pnpm build`（dist 重新构建提交）。
