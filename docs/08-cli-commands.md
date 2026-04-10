# 08 — CLI 命令设计

---

## 已实现命令

```bash
python main.py init                              # 初始化数据库

# 目标管理
python main.py goal new <title> [--domains ...]   # 创建学习目标，递归拆解
python main.py goal list                          # 列出所有目标
python main.py goal tree <goal-id>                # 树状图展示知识图谱
python main.py goal nodes <goal-id>               # 按拓扑序列出原子节点
python main.py goal remove <goal-id>              # 删除目标及关联数据
python main.py goal export <goal-id>              # 导出 draw.io 节点图
python main.py goal assess <goal-id>              # 初始知识评估（自适应探测，跳过已知节点）

# 状态总览
python main.py status                             # 今日学习状态 + 下一个建议学习节点

# 学习
python main.py learn start <node-id>              # 开始学习：生成大纲 → 进入苏格拉底对话
python main.py learn chat <node-id>               # 继续未完成的苏格拉底对话
python main.py learn progress <node-id>           # 查看当前学习进度

# 考试
python main.py exam start <node-id>               # 开始考试（可跳过学习直接进入）
python main.py exam review <exam-id>              # 查看某次考试的结果详情

# 错题本
python main.py errors list [--node <node-id>]     # 查看错题本（可按节点过滤）
python main.py errors review <node-id>            # 重做某节点的历史错题

# 复习（Ebbinghaus 间隔复习）
python main.py review list                        # 查看复习队列（优先级排序）
python main.py review start                       # 开始最高优先级复习
python main.py review start <node-id>             # 对指定节点进行复习
```

## 完整用户学习流程

```
goal new "学会 Kubernetes"          ← 拆解成知识图谱
goal assess <goal-id>               ← 初始评估，跳过已知内容
learn start <node-id>               ← 逐节点苏格拉底学习
exam start <node-id>                ← 学完后考试
review list                         ← 查看到期复习
review start                        ← 执行 Ebbinghaus 复习
```
