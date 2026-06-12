# 21 — 学习链路 Bug 修复与 schema 迁移机制

## 范围

修复探索审查发现的 5 个链路 bug（B3–B7），并为后续 schema 演进（doc 23 的
v2 加表/加列）铺设 `PRAGMA user_version` 迁移机制。

## B3 — 艾宾浩斯轮次记账不一致

**现象**：`reviewer.py` / `services/review.py` 完成复习时用
`next_round = review_round + 1` 调 `dag.next_review_interval()`，但该函数在
score < 0.5 时无视轮次直接返回 round-1 间隔（1 天）——持久化的
`review_round` 与实际间隔来源脱节。

**决定语义（单一事实源）**：`dag.next_review_state(review_round, score,
threshold) -> (next_round, interval_days)`：

| 得分 | next_round | interval |
| ---- | ---------- | -------- |
| score < 0.5（严重失败） | **重置为 1** | `intervals[0]`（1 天） |
| 0.5 ≤ score < threshold（及格未达标） | **保留当前轮次** | 当前轮 base 间隔减半（≥1 天） |
| score ≥ threshold（通过） | round + 1 | 下一轮 base 间隔 |

两个调用点（`agents/reviewer.py`、`services/review.py`）统一改用该函数，
`create_review(review_round=…)` 与 `complete_review(next_interval_days=…)`
使用同一返回值。`next_review_interval` 保留（`_finalize_exam` 等首考路径
仍用），但复习完成路径一律走 `next_review_state`。

**存量数据**：已存在的 review_schedule 行不回填，新语义仅影响新写入。

## B4 — 错题本阈值与节点严格度脱钩

`examiner._finalize_exam` 原硬编码 `score >= 0.6` 跳过错题本写入；critical
节点（threshold 0.95）的 0.7 分题被漏记。改为使用该函数内已计算的节点
`threshold`：凡单题得分低于节点掌握阈值即入错题本。

## B5 — 畸形题留下孤儿考试记录

`generate_questions` 原只校验"列表非空"；缺 `question` / `expected_answer`
字段的题会在 `create_exam` 之后的持久化循环里 KeyError，留下带部分题目的
孤儿 attempt。改为在 normalise 循环中剔除畸形题，剔空则抛 `ValueError`——
一切发生在 `services/exam.start_exam` 调 `create_exam` 之前。

## B6 — 大纲重生成遗留孤儿助记锚点

`teacher.generate_outline` 原仅在 `mnemonic_strategy` 非空时清理旧锚点；
用户档案被删后重生成大纲会遗留上一版 section 布局的孤儿锚点。改为持久化
大纲后**无条件**调用 `delete_mnemonic_anchors`。

## B7 — 落地 needs_review 状态（原死状态）

`user_knowledge_state.status` 定义了 `needs_review` 但无任何写入点，而
CLI / drawio / print / GUI 四处展示端已在消费。落地两个写入点：

1. **复习失败**：`reviewer.run_review_loop` 与 `services/review.finish_review`
   的失败分支，在 `_finalize_exam`（写 `learning`）之后将状态覆写为
   `needs_review`——区别"从未掌握"（learning）与"掌握过但复习失败"。
2. **掌握衰减降级**：`services/review.get_queue` 组队列时，对
   `mastered` 但 `effective_mastery < mastery_threshold` 的节点降级落库为
   `needs_review`（复用 `dag.effective_mastery`）。

> 设计取舍：降级选择"落库"而非仅展示层计算，因为 print / CLI / GUI 多端
> 需要一致视图。`services/review.py` 的文件级注释同步更新（get_queue 不再
> 纯只读）。首考失败仍是 `learning`，不使用 needs_review。

## schema 迁移机制

`database.py` 新增：

- `SCHEMA_VERSION = 1`（本文档基线；doc 23 升至 2）。
- `_migrate(conn)`：读 `PRAGMA user_version`，低于当前版本时按序执行迁移
  步骤（v1 基线 = 重放幂等的 `SCHEMA_SQL`，`CREATE TABLE IF NOT EXISTS`
  补齐缺表），最后写回 `user_version`。
- `get_connection()` 对每个 DB 路径每进程惰性迁移一次——已有库被任何入口
  打开即自动升级；`init_db()` 显式走同一路径。
- 加列类迁移（v2 起）用 `ALTER TABLE … ADD COLUMN`，保持幂等可重入。
