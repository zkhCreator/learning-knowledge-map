# 23 — 考题正反校验与记忆全链路

## 范围

学习链路两项增强（schema v2）：

1. **考题/评分的正反校验**：出题后反向校验 agent 审题，评分后抽样复核。
2. **记忆方案贯穿全链路**：考前回忆提示、对话锚点表注入、锚点 effectiveness
   回写、错题驱动出题。

## 4a — 考题正反校验

复用既有 Forward/Reverse 模式（decomposer / teacher 的 OUTLINE_REVERSE）：

- `examiner.validate_questions(node, outline_sections, questions, model)`
  （`EXAM_REVERSE_SYSTEM`）：校验三点——大纲覆盖度、标准答案正确性、难度
  与节点 strictness 匹配。返回 `{approved, issues, corrections}`，
  `corrections` 以题目序号为键给出修正后的 `expected_answer`。
- 接线在 `services/exam.start_exam`（skill / 工具侧入口）：
  生成 → 校验；未通过 → 带 issues 重生成一次 → 再校验；仍未通过 → 应用
  corrections 内联修正后放行。校验结论在 `create_exam` 成功后落
  `exam_validations` 审计表。
- **评分抽样复核** `examiner.spot_check_scores(exam_id, node, sample_n)`：
  选取距节点阈值最近（最"边界"）的 N 题重评；分差 > 0.2 才以复核分覆盖
  （保守策略）。接线在 `skills/exam-start/scripts/exam_cli.py score`——
  评完所有题、finalize 之前。
- **Web 数据平面不受影响**：校验与抽检只存在于工具侧路径；
  `workflow_api.py` 的 `exam_finish` 仍是纯数据 finalize（LDG_WEB_MODE
  闸门不变）。
- 成本开关：`EXAM_VALIDATE=0` 跳过反向校验，`EXAM_SPOT_CHECK=0` 跳过抽检
  （默认皆开）。

## 4b — 记忆方案全链路

现状：苏格拉底对话已注入大纲内嵌助记、复习前已有主动回忆。补齐缺口：

- **考前提示**：`start_exam` 返回可选 `mnemonic` 字段（复用
  `mnemonic.get_retrieval_context`，纯 DB 读，无档案/锚点时为 None）；
  `get_exam_view` 同步带出（GUI ExamView 展示"先回忆"折叠卡，doc 24）；
  `exam_cli generate` 打印回忆提示。
- **对话锚点表注入**：`teacher.chat_turn` 改为同时从
  `db.get_mnemonic_anchors` 读表注入（不再只依赖大纲 JSON 内嵌），旧会话
  与重生成大纲也能引用锚点。
- **effectiveness 回写**：复习收尾（`reviewer.run_review_loop` 与
  `services/review.finish_review`）按本次总分对该节点全部锚点做 EMA 更新：
  `new = 0.7*old + 0.3*score`，首次为 score。`db.update_mnemonic_anchor`
  已存在，纯接线。
- **错题驱动出题**：`generate_questions` 加 `error_entries` 参数；
  `start_exam` 自动取该节点错题本注入（首考无错题 = 行为不变，复习再考
  自然带上）。提示词要求 30–50% 题目针对历史错点；此类题落库
  `exam_questions.origin='error_driven'`，其余 `'outline'`。

## schema v2（用 doc 21 的迁移机制）

- 新表 `exam_validations(id, exam_id→exam_attempts, verdict, issues JSON,
  created_at)`。
- `exam_questions` 加列 `origin TEXT`（'outline' | 'error_driven'，旧行
  NULL 视同 outline）。
- `SCHEMA_VERSION = 2`；v1→v2：重放 SCHEMA_SQL（补新表）+
  `ALTER TABLE exam_questions ADD COLUMN origin`（幂等防护：先查
  table_info）。
