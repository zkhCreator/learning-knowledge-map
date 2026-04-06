# Learning Directed Graph

递归学习图谱 CLI。当前可执行入口是 `python main.py ...`。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

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
- `OPENAI` 这一路如果只给裸 domain，会自动补成 `/v1`。

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
| `ANTHROPIC_API_KEY` | 无 | Anthropic 单独 key，作为 fallback |
| `ANTHROPIC_BASE_URL` | 无 | Anthropic 单独 domain，作为 fallback |
| `OPENAI_API_KEY` | 无 | OpenAI 单独 key，作为 fallback |
| `OPENAI_BASE_URL` | `https://api.openai.com` | OpenAI 单独 domain，作为 fallback |

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

### 4. 查看目标树

```bash
python main.py goal tree "<goal_id_or_prefix>" [--user "<user_id>"]
```

参数：
- `goal_id_or_prefix`：必填，完整 Goal ID 或前缀
- `--user`, `-u`：可选，用户 ID，默认 `default`

说明：
- `--user` 当前不参与解析目标 ID，但命令保留了这个参数形态。

### 5. 查看学习顺序节点

```bash
python main.py goal nodes "<goal_id_or_prefix>" [--user "<user_id>"]
```

参数：
- `goal_id_or_prefix`：必填，完整 Goal ID 或前缀
- `--user`, `-u`：可选，用户 ID，默认 `default`

### 6. 查看今日学习状态

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
python main.py goal tree <goal-id>
python main.py goal nodes <goal-id>
```

### 查看当天状态

```bash
python main.py status
```
