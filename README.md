# Learning Directed Graph

递归学习图谱 CLI。当前标准入口是 `python main.py ...`。

兼容入口：
- `python learn ...` 会转发到完整 CLI
- `python goal ...` 会直接进入 `goal` 子命令

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

如果直接运行命令时看到 `ModuleNotFoundError: No module named 'typer'`，说明当前解释器还没安装本项目依赖，先执行上面的安装步骤。

## 配置

项目会从仓库根目录的 `.env` 读取环境变量。

### 推荐配置：统一走中转服务商

```env
LLM_BASE_URL=https://your-relay.example.com
LLM_API_KEY=your-relay-key
DEFAULT_MODEL=claude-sonnet-4-6
```

说明：
- `LLM_BASE_URL` 和 `LLM_API_KEY` 会同时给 Anthropic 协议和 OpenAI-compatible 协议复用。
- 模型协议按 `DEFAULT_MODEL` 自动判断：
  - `claude-*` 走 Anthropic 协议
  - 其他模型走 OpenAI-compatible 协议

### 可用环境变量

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `LLM_BASE_URL` | 无 | 推荐使用的统一中转 domain |
| `LLM_API_KEY` | 无 | 推荐使用的统一中转 key |
| `DEFAULT_MODEL` | `claude-sonnet-4-6` | 默认模型名 |
| `DB_PATH` | `data/learning.db` | SQLite 数据库路径 |
| `MAX_DECOMPOSE_DEPTH` | `6` | 最大递归拆解深度 |
| `MAX_DECOMPOSE_RETRIES` | `2` | 每层拆解最大重试次数 |
| `ATOM_MAX_MINUTES` | `15` | 原子知识点最大预估学习时间 |

## 命令总览

```bash
python main.py --help
python main.py init
python main.py init --help
python main.py goal --help
python main.py goal new "学会 Kubernetes 集群管理" --domains "Linux,Docker"
python main.py goal new --help
python main.py goal list
python main.py goal list --help
python main.py goal remove <goal-id>
python main.py goal remove --help
python main.py goal export <goal-id>
python main.py goal export --help
python main.py goal tree <goal-id>
python main.py goal tree --help
python main.py goal nodes <goal-id>
python main.py goal nodes --help
python main.py status
python main.py status --help
```

## Help 快速查看

```bash
python main.py --help
python main.py init --help
python main.py goal --help
python main.py goal new --help
python main.py goal list --help
python main.py goal remove --help
python main.py goal export --help
python main.py goal tree --help
python main.py goal nodes --help
python main.py status --help
```

说明：
- 根命令和 `goal` 子命令现在都开启了 `no_args_is_help=True`。
- 所以直接运行 `python main.py` 或 `python main.py goal` 时，也会显示对应层级的帮助。

## 命令与参数

### 1. 初始化数据库

```bash
python main.py init [--verbose]
```

参数：
- `--verbose`, `-v`：同时把 DEBUG 日志打印到终端

说明：
- 会先校验当前模型对应的 API key，再初始化数据库。

### 2. 创建学习目标并拆解

```bash
python main.py goal new "<title>" [--domains "<d1,d2,...>"] [--user "<user_id>"] [--verbose]
```

参数：
- `title`：必填，学习目标
- `--domains`, `-d`：可选，用户已知领域，逗号分隔
- `--user`, `-u`：可选，用户 ID，默认 `default`
- `--verbose`, `-v`：可选，同时打印 DEBUG 日志

示例：

```bash
python main.py goal new "学会 Kubernetes 集群管理" --domains "Linux,Docker"
python main.py goal new "理解 Transformer" -d "Python,线性代数" -u alice -v
```

### 3. 列出学习目标

```bash
python main.py goal list [--user "<user_id>"]
```

参数：
- `--user`, `-u`：可选，用户 ID，默认 `default`

### 4. 删除学习目标

```bash
python main.py goal remove "<goal_id_or_prefix>" [--user "<user_id>"] [--yes]
```

参数：
- `goal_id_or_prefix`：必填，完整 Goal ID 或前缀
- `--user`, `-u`：可选，用户 ID，默认 `default`
- `--yes`, `-y`：可选，跳过确认提示，直接删除

说明：
- 会同时删除该目标下的知识点、依赖边、学习状态和复习计划。

### 5. 查看目标树

```bash
python main.py goal tree "<goal_id_or_prefix>" [--user "<user_id>"]
```

参数：
- `goal_id_or_prefix`：必填，完整 Goal ID 或前缀
- `--user`, `-u`：可选，用户 ID，默认 `default`

说明：
- `--user` 会参与目标 ID 解析，只会在该用户的目标中匹配前缀。

### 6. 导出 draw.io 节点图

```bash
python main.py goal export "<goal_id_or_prefix>" [--user "<user_id>"] [--output "<path.drawio>"] [--atomic-only]
```

参数：
- `goal_id_or_prefix`：必填，完整 Goal ID 或前缀
- `--user`, `-u`：可选，用户 ID，默认 `default`
- `--output`, `-o`：可选，输出文件路径；默认写到 `exports/`
- `--atomic-only`：可选，只导出原子知识点

说明：
- 输出格式为原生 `.drawio` XML，可直接在 draw.io / diagrams.net 打开。
- 默认同时导出拆解树关系和 prerequisite / analogy 边。

### 7. 查看学习顺序节点

```bash
python main.py goal nodes "<goal_id_or_prefix>" [--user "<user_id>"]
```

参数：
- `goal_id_or_prefix`：必填，完整 Goal ID 或前缀
- `--user`, `-u`：可选，用户 ID，默认 `default`

### 8. 查看今日学习状态

```bash
python main.py status [--user "<user_id>"] [--verbose]
```

参数：
- `--user`, `-u`：可选，用户 ID，默认 `default`
- `--verbose`, `-v`：可选，同时打印 DEBUG 日志

## 当前限制

- 目前没有 CLI 级别的 `--model`、`--openai`、`--claude` 参数。
- 当前模型切换方式是改 `.env` 里的 `DEFAULT_MODEL`。
- 日志文件默认写到 `data/learning.log`。

## 常用流程

### 初始化

```bash
python main.py init
```

### 创建目标并拆解

```bash
python main.py goal new "学会 Kubernetes 集群管理" --domains "Linux,Docker"
```

### 查看目标图谱

```bash
python main.py goal list
python main.py goal remove <goal-id>
python main.py goal export <goal-id>
python main.py goal tree <goal-id>
python main.py goal nodes <goal-id>
```

### 查看当天状态

```bash
python main.py status
```
