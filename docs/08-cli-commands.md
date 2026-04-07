# 08 — CLI 命令设计

---

## 已实现命令

```bash
python main.py init                              # 初始化数据库
python main.py goal new <title> [--domains ...]   # 创建学习目标，递归拆解
python main.py goal list                          # 列出所有目标
python main.py goal tree <goal-id>                # 树状图展示知识图谱
python main.py goal nodes <goal-id>               # 按拓扑序列出原子节点
python main.py status                             # 今日学习状态
```

## 待实现命令（学习 + 考试）

```bash
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
```
