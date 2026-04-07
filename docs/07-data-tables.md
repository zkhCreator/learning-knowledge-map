# 07 — 新增数据表（学习 + 考试 + 错题本）

---

以下表为 §06 节点学习流程服务，补充 §03 中的基础 schema。

## 表概览

| 表名 | 用途 | 所属阶段 |
| ---- | ---- | -------- |
| `node_outlines` | 每个节点的学习大纲 | 学习 |
| `learning_sessions` | 苏格拉底对话会话 | 学习 |
| `chat_messages` | 对话历史 | 学习 |
| `exam_attempts` | 考试记录 | 考试 |
| `exam_questions` | 单题记录 | 考试 |
| `error_notebook` | 错题本 | 考试 |

## 详细字段

### node_outlines

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| id | TEXT PK | UUID |
| node_id | TEXT FK | 关联 knowledge_nodes |
| user_id | TEXT | 默认 'default' |
| sections | TEXT (JSON) | `[{index, title, content, needs_search, sources[], analogy, analogy_source_node, covered}]` |
| status | TEXT | `draft` → `validated` → `active` → `completed` |
| created_at | TEXT | ISO 时间戳 |
| UNIQUE(node_id, user_id) | | 每个用户每个节点一份大纲 |

### learning_sessions

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| id | TEXT PK | UUID |
| node_id | TEXT FK | 关联 knowledge_nodes |
| outline_id | TEXT FK | 关联 node_outlines |
| user_id | TEXT | 默认 'default' |
| progress | REAL | 0.0-1.0，已覆盖小节比例 |
| covered_sections | TEXT (JSON) | `[1, 3, 4]` 已覆盖的 section index 列表 |
| status | TEXT | `active` / `summarised` / `completed` |
| started_at | TEXT | 会话开始时间 |
| updated_at | TEXT | 最后更新时间 |

### chat_messages

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| id | TEXT PK | UUID |
| session_id | TEXT FK | 关联 learning_sessions |
| role | TEXT | `user` / `assistant` |
| content | TEXT | 消息内容 |
| created_at | TEXT | 消息时间 |

### exam_attempts

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| id | TEXT PK | UUID |
| node_id | TEXT FK | 关联 knowledge_nodes |
| outline_id | TEXT FK | 关联 node_outlines（可选，跳过学习时为 null） |
| user_id | TEXT | 默认 'default' |
| total_score | REAL | 所有题目的平均分 |
| passed | INTEGER | 1=通过, 0=未通过 |
| started_at | TEXT | 考试开始时间 |
| finished_at | TEXT | 考试结束时间 |

### exam_questions

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| id | TEXT PK | UUID |
| exam_id | TEXT FK | 关联 exam_attempts |
| question_type | TEXT | `multiple_choice` / `short_answer` / `scenario` / `distinction` |
| question | TEXT | 题目内容 |
| options | TEXT (JSON) | 选择题的选项，其他题型为 null |
| expected_answer | TEXT | 期望答案 |
| user_answer | TEXT | 用户的回答 |
| score | REAL | 0.0-1.0 |
| source_section | INTEGER | 来源大纲小节 index（扩展题为 null） |
| is_expansion | INTEGER | 1=超出大纲的扩展题, 0=大纲内的题 |
| created_at | TEXT | 出题时间 |

### error_notebook

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| id | TEXT PK | UUID |
| user_id | TEXT | 默认 'default' |
| node_id | TEXT FK | 来源知识节点 |
| exam_id | TEXT FK | 来源考试 |
| question_id | TEXT FK | 来源题目 |
| source_section_title | TEXT | 大纲小节标题 |
| error_type | TEXT | `memory_confusion` / `boundary_unclear` / `fundamental_misunderstanding` / `incomplete` |
| question | TEXT | 原题 |
| user_answer | TEXT | 用户的错误回答 |
| correct_answer | TEXT | 正确答案 |
| explanation | TEXT | 为什么错 + 如何修正 |
| related_node_ids | TEXT (JSON) | 关联知识节点 ID 列表 |
| related_node_titles | TEXT (JSON) | 关联知识节点标题列表 |
| review_count | INTEGER | 复习次数 |
| last_reviewed | TEXT | 上次复习时间 |
| created_at | TEXT | 记录时间 |
