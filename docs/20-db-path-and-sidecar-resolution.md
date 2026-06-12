# 20 — DB 路径与 sidecar 统一解析

## 背景与问题

skill 化（doc 22）要求每个 skill 可以脱离本仓库独立安装运行，"仓库根"不再是可依赖的锚点。
现状存在 4 种互不一致的默认 DB 路径实现，其中两处是分层迁移（doc 17）的遗留 bug：

| 入口 | 旧默认 | 问题 |
| ---- | ------ | ---- |
| `src/infrastructure/config.py` | `Path(__file__).parent.parent / "data/learning.db"` | 迁移后实际指向 `src/data/learning.db`（错误） |
| `src/infrastructure/web_link.py` | `parents[1] / "data/.web_url"` | 同上，读 `src/data/.web_url` |
| `server.js` | `<仓库根>/data/.web_url` | 与 web_link.py 读端不一致 → sidecar 深链机制实际断裂 |
| `export_graph.py` / `print_graph.py` | `.git 根/data/learning.db` | 依赖 .git 存在 |
| `persist_result.py` | `.git 根/learning.db`，且缺 `DB_PATH` 环境变量层 | 与其余入口不一致 |
| `exam_cli.py` | `--db` 缺省时无条件覆盖已有 `DB_PATH` 环境变量 | 违反声明的优先级 |

## 统一规则（canonical）

所有入口一律按同一优先级解析数据库路径：

1. **显式参数**（`--db` CLI 参数或函数入参）
2. **`DB_PATH` 环境变量**
3. **默认：`Path.cwd() / "learning.db"`**（当前工作目录根）

补充语义（沿用 persist_result.py 既有约定，推广到所有写入口）：

- 显式参数指向目录时，取 `该目录 / learning.db`；
- 显式参数带 `.db` / `.sqlite` / `.sqlite3` 后缀时，按文件路径使用；
- 相对路径相对 CWD 解析。

默认选 CWD 根（而非 `data/` 子目录或仓库根）的理由：生成的 db 是单独提供给
AI / 学习者使用的工件，需要在用户当前目录根部直接可见；安装为独立 skill 后
没有"仓库根"概念，CWD 是唯一对用户直观且稳定的锚点。

### sidecar 文件（.web_url）

sidecar **永远跟随已解析的 db 所在目录**：`<resolved_db>.parent / ".web_url"`。

- 写端：`server.js` 用 `loadGraph` 返回 payload 中的 `db_path`（export_graph.py
  已输出解析后的绝对路径）计算 sidecar 位置；
- 读端：`web_link.py` 基于 `config.DB_PATH.parent` 惰性计算，两端天然一致。

## 实现收口

- canonical 实现：`src/infrastructure/config.py::resolve_db_path(explicit=None)`，
  模块级 `DB_PATH` 用它初始化。
- `export_graph.py` / `print_graph.py` 保持纯 stdlib 自治（文件级 Non-Goal），
  内部 `resolve_db_path` 改为同一规则，由 `tests/test_db_path_resolution.py`
  的跨实现一致性用例钉住，防止漂移。
- `persist_result.py` 补 `DB_PATH` 环境变量层，默认改 CWD。
- `exam_cli.py::_set_db_path`：`--db` > 已有 `DB_PATH` env > CWD 默认，
  不再无条件覆盖环境变量。

## 行为注意

- **读场景不静默新建库**：print / serve / export 在库不存在时抛
  `FileNotFoundError`（既有行为保留）；SKILL.md 提示用户先确认路径。
- 写场景（persist / exam / init）按需创建父目录。
- 既有用户若依赖旧默认 `仓库根/data/learning.db`，需显式传 `--db` 或设
  `DB_PATH`；各 SKILL.md / README 示例已同步更新。
