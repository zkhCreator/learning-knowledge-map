# 05 — 艾宾浩斯复习调度

---

## 复习间隔模型

基础间隔表:

| 复习轮次 | 1 | 2 | 3 | 4  | 5  | 6  |
| -------- | - | - | - | -- | -- | -- |
| 间隔天数 | 1 | 3 | 7 | 14 | 30 | 90 |

根据掌握质量动态调整:
- **优秀** (score >= 0.8): 正常推进到下一个间隔
- **一般** (0.5 - 0.8): 间隔缩短 50%
- **差** (< 0.5): 状态退回 `needs_review`，从间隔 1 重新开始，正向 Agent 重新讲解

## mastery_score 衰减机制

```
effective_mastery = raw_score × exp(-days_elapsed / stability)
```

- `raw_score`: 最近一次 QA 的原始得分
- `days_elapsed`: 距上次复习的天数
- `stability`: 遗忘速率参数
  - 初始值 = 1.0
  - 每次成功复习（score >= threshold）后增长: `stability *= 1.5`
  - 复习失败后衰减: `stability *= 0.7`

**节点完成判定**:
```
is_node_complete(node, user):
    effective = raw_score × exp(-days_elapsed / stability)
    return effective >= node.mastery_threshold
```

这统一了两个需求: mastery_score 随时间衰减 + 不同节点有不同完成标准。

## 严格度分级与阈值

| 等级         | 名称     | mastery_threshold | 适用场景                           |
| ------------ | -------- | ----------------- | ---------------------------------- |
| `critical`   | 严格掌握 | >= 0.95           | 医疗、安全、法律合规等高风险领域     |
| `standard`   | 标准掌握 | >= 0.80           | 大多数技术知识、学科概念             |
| `familiarity`| 了解即可 | >= 0.60           | 背景知识、概览性内容                |

**对 critical 节点的特殊处理**:
- QA 验证更严格，反向 Agent 设计易混淆的陷阱题
- 未达阈值时不允许进入后续节点
- 复习频率更高（间隔缩短 50%）

**阈值来源**: 正向 Agent 拆解时自动标注（检测到高风险领域自动标 critical）+ 用户可手动覆盖。

## CLI 会话中的复习优先级

每次 CLI 会话开始时，系统检查复习队列:

```
优先级排序:
1. 逾期复习（overdue） — 尤其是 critical 节点
2. 今日复习（pending, scheduled_at <= today）
3. 继续新知识学习
```

这保证遗忘曲线不会被新内容的学习打断。
