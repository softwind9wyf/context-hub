# Context Hub

> 多 Agent 共享工作记忆 —— 让 AI Agent 之间真正互通信息

Context Hub 是为 [OpenClaw](https://github.com/openclaw/openclaw) 设计的**多 Agent 共享记忆基础设施**。它为所有 Agent 提供统一的记忆存储、知识图谱和跨 Agent 信息检索能力——就像一个公司里的**公共黑板**。

## 为什么需要 Context Hub？

OpenClaw 中每个 Agent 有自己的 memory 文件（`MEMORY.md`、`memory/*.md`），但这些信息是**孤岛**——Agent A 不知道 Agent B 今天做了什么决策、发现了什么、认识了谁。

Context Hub 解决的就是这个**跨 Agent 信息互通**问题：

```
Agent A ──上报──→ ┌──────────────┐ ←──检索── Agent B
Agent C ──上报──→ │ Context Hub  │ ←──检索── Agent D
                  │  共享工作记忆  │
Memory 文件 ─摄入→ │  实体 & 关系  │
                  │  知识图谱     │
                  └──────────────┘
                       ↑
                  每天自动扫描摄入
```

### 与 OpenClaw 原生机制的关系

| 机制 | 定位 | 生命周期 | 可见性 |
|------|------|---------|--------|
| session 对话上下文 | 感觉记忆 | 单次会话 | 当前 session |
| Agent memory 文件 | 私人笔记 | 持久化 | 单个 Agent |
| MEMORY.md | 个人长期记忆 | 持久化 | 单个 Agent |
| **Context Hub** | **共享工作记忆** | **持久化** | **所有 Agent** |

类比：memory 文件是每个人的**笔记本**，Context Hub 是公司的**共享 Wiki + 通讯录 + 项目看板**。

## 数据模型

Context Hub 管理五类数据，支持**五维统一检索**：

| 数据 | 说明 | 生命周期 |
|------|------|---------|
| **短期记忆** (short_term) | 事件、决策、待办、对话摘要 | 天/周级，自动过期 |
| **长期记忆** (long_term) | 人物、项目、知识、经验、偏好 | 永久，权重衰减 |
| **Memory Sources** | Agent memory 文件的直接索引 | 跟随文件更新 |
| **共享笔记** (memos) | 跨 Agent 共享的洞察、事实、问题 | 可设过期 |
| **实体 & 关系** | 人物、项目、工具、组织的知识图谱 | 永久 |
| **活动流** (agent_activity) | Agent 上报的任务完成、决策、发现 | 永久 |

## 功能

### 🧠 短期记忆 (Short-term Memory)

- **类型：** event / conversation / todo / decision / note
- 支持重要性评分（0-1）和自动过期
- 高重要性记忆可提炼为长期记忆

### 💎 长期记忆 (Long-term Memory)

- **类型：** fact / person / project / experience / preference / knowledge / relation
- 支持置信度评分，不常访问的记忆自动衰减
- 事实可标注验证时间

### 👤 实体 & 关系 (Entities & Relations)

- 实体类型：person / project / org / tool / concept / location
- 支持别名（aliases），模糊查找
- 关系图查询：`graph <entity_name>`

### 🔍 统一检索 (Recall)

三种搜索模式，互补使用：

- **FTS 全文搜索** — 基于 jieba 中文分词，精确匹配
- **向量语义搜索** — 基于 bge-m3 embedding，自然语言查询
- **混合搜索** — FTS + 向量，最推荐

### 🧹 遗忘机制 (Forgetting)

- **过期清理：** 短期记忆到期自动清除
- **权重衰减：** 长期记忆 30 天未访问，置信度自动降低

### 🔄 整合 (Consolidation)

- 短期 → 长期的提炼候选推荐
- 基于重要性和时间筛选待整合记忆

## 技术栈

- **存储：** SQLite (WAL mode)
- **全文搜索：** FTS5 + jieba 中文分词
- **向量检索：** Ollama bge-m3 (1024 维)
- **语言：** Python 3，零外部框架依赖（除 jieba）
- **平台：** macOS / Linux

## 快速开始

### 前置条件

1. **Python 3.10+**
2. **Ollama** 运行中，已加载 bge-m3 模型：
   ```bash
   ollama pull bge-m3
   ```
3. **jieba**：
   ```bash
   pip3 install jieba
   ```

### 初始化

```bash
python3 hub.py init
```

### 基本使用

```bash
# 添加短期记忆
python3 hub.py short-add event "项目会议" "讨论了 Q2 OKR，确定三个重点方向" --importance 0.8 --expire 7

# 添加长期记忆
python3 hub.py long-add person "张三" "后端工程师，负责 API 开发" --importance 0.8

# 添加实体和关系
python3 hub.py entity-add "张三" person "zhangsan" "后端工程师"
python3 hub.py rel-add "张三" "Q2 OKR" "works_on"

# 统一检索
python3 hub.py recall "后端开发进度" --mode hybrid

# 查看知识图谱
python3 hub.py graph "张三"

# 状态查看
python3 hub.py status
```

### API 使用

也可以在 Python 中直接调用：

```python
from hub import short_add, long_add, recall, entity_add, relation_add

# 写入
long_add("preference", "编码风格", "偏好 Python，4 空格缩进，type hints", importance=0.8)

# 检索
results = recall("我喜欢什么编程语言", mode="vector")
for r in results:
    print(f"[{r['scope']}] {r['title']}: {r['content']}")
```

## 命令参考

```
hub.py init                                    初始化数据库
hub.py status                                  查看状态

短期记忆:
  hub.py short-add <type> <title> <content> [options]
      --source src  --tags tag1,tag2  --importance 0.8  --expire 7
  hub.py short-list [--type ...]               列出短期记忆
  hub.py short-get <id>                        查看详情
  hub.py short-del <id>                        删除

长期记忆:
  hub.py long-add <type> <title> <content> [options]
      --source src  --tags tag1,tag2  --importance 0.8  --confidence 0.9
  hub.py long-list [--type ...]                列出长期记忆
  hub.py long-get <id>                         查看详情
  hub.py long-del <id>                         删除
  hub.py long-update <id> [--content ...]      更新

共享笔记:
  hub.py memo-add <type> <title> <content> [options]
      --agent xxx  --tags tag1,tag2  --importance 0.8  --expire 7
  hub.py memo-list [--agent xxx] [--type ...]  列出笔记
  hub.py memo-get <id>                         查看详情
  hub.py memo-del <id>                         删除

活动流:
  hub.py activity-report <agent> <type> <title> <content> [--session-id xxx]
  hub.py activity-list [--agent xxx]            列出活动

实体 & 关系:
  hub.py entity-add <name> <type> [aliases] [description]
  hub.py entity-find <name>                    查找实体
  hub.py entity-list [--type ...]              列出实体
  hub.py rel-add <from> <to> <type> [description]
  hub.py graph <entity_name>                   查看关系图

检索:
  hub.py recall <query> [--limit 10] [--mode hybrid|fts|vector]

维护:
  hub.py consolidate                           查看整合候选
  hub.py forget                                清理过期 + 衰减权重

自动摄入:
  ingest.py                                    扫描所有 agent memory 并建索引
      --agent xxx                              只处理指定 agent
      --dry-run                                预览模式
      --force                                  强制重新索引
```

## 设计理念

1. **本地优先** — 数据存在本地 SQLite，不上云，不泄露
2. **零 LLM 依赖摄入** — memory 文件直接索引，不经过 LLM，零信息损耗、秒级完成
3. **渐进增强** — Agent 主动上报 + 自动摄入，越用越丰富
4. **人类可读** — 所有数据都可以直接用 SQL 查询和调试
5. **MCP 原生** — 通过 MCP 协议集成，Agent 像调用工具一样使用

## OpenClaw 集成

Context Hub 作为 OpenClaw 多 agent 共享记忆系统，为所有 agent 提供统一的记忆存储和检索能力。

### 安装

```bash
cd /path/to/context-hub
chmod +x setup.sh
./setup.sh
```

详细安装说明请参考 [README_SETUP.md](README_SETUP.md)。

### MCP 工具列表

Context Hub 通过 MCP 协议提供以下工具：

| 工具 | 功能 |
|------|------|
| `ctx_recall` | 统一检索（关键词/语义/混合） |
| `ctx_short_add` | 添加短期记忆（事件/对话/待办/决策） |
| `ctx_long_add` | 添加长期记忆（人物/项目/知识/偏好） |
| `ctx_memo_add` | 添加跨 agent 共享笔记 |
| `ctx_activity_report` | 上报 agent 活动（任务完成/决策/错误） |
| `ctx_entity_add` | 添加实体（人物/项目/工具/组织） |
| `ctx_rel_add` | 添加实体关系 |
| `ctx_graph` | 查询实体关系图 |
| `ctx_status` | 查看系统状态 |
| `ctx_forget` | 执行遗忘清理 |
| `ctx_consolidate` | 查看短期→长期整合候选 |

### 自动摄入

系统每天自动扫描各 Agent 的 memory 文件，按 section 切分后直接建立 FTS + 向量索引（不经过 LLM，零信息损耗）：

- **时间**：每天凌晨 2:00（OpenClaw cron）
- **来源**：`~/.openclaw/workspace-{agent-name}/MEMORY.md` 和 `memory/*.md`
- **处理方式**：按 markdown 标题切分 → jieba 分词 + bge-m3 embedding → 直接索引

### 使用示例

在 agent 中调用 MCP 工具：

```python
# 添加长期记忆
ctx_long_add(
    mem_type="person",
    title="张三",
    content="后端工程师，负责 API 开发",
    importance=0.8
)

# 检索
ctx_recall(query="后端开发", mode="hybrid", limit=10)

# 上报活动
ctx_activity_report(
    agent_name="my-agent",
    activity_type="task_completed",
    title="完成 API 重构",
    content="重构了用户认证 API，提升了 30% 性能"
)
```

### Heartbeat 上报

在 agent 的 heartbeat 中，可以使用 Context Hub 共享信息：

```markdown
# Context Hub Heartbeat 上报

如果有值得共享的信息，通过 MCP 工具上报：
- ctx_activity_report: 关键决策、任务完成、重要发现
- ctx_memo_add: 跨 agent 共享的笔记和洞察
- ctx_entity_add: 新发现的人物、项目、工具、组织
```

## License

MIT
