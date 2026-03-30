# Context Hub 安装指南

本文档详细说明如何将 Context Hub 安装并集成到 OpenClaw 系统中。

## 前提条件

### 必需

| 依赖 | 版本要求 | 安装方法 |
|------|---------|---------|
| Python | 3.10+ | `brew install python@3.12` |
| Ollama | 运行中 | `brew install ollama && ollama serve` |
| bge-m3 模型 | - | `ollama pull bge-m3` |
| jieba | - | `pip3 install jieba` |

### 可选

| 依赖 | 用途 |
|------|------|
| GLM-4 / qwen2.5 / llama3 | 自动摄入时的 LLM 信息提取 |

## 快速安装

```bash
cd /path/to/context-hub
chmod +x setup.sh
./setup.sh
```

安装脚本会自动完成所有配置。

## 手动安装

如果需要手动安装，请按以下步骤操作：

### Step 1: 初始化数据库

```bash
python3 hub.py init
```

数据库位置：`~/.openclaw/workspace-knowledge-keeper/context-hub/hub.db`

### Step 2: 安装 MCP 依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install mcp
deactivate
```

### Step 3: 注册 MCP Server

编辑 OpenClaw 的 MCP 配置文件（通常为 `~/.openclaw/mcpServers.json`）：

```json
{
  "context-hub": {
    "command": "/绝对路径/to/context-hub/.venv/bin/python",
    "args": ["/绝对路径/to/context-hub/mcp_server.py"],
    "type": "stdio"
  }
}
```

**注意**：路径必须是绝对路径。

### Step 4: 配置定时任务

#### 方式 A: OpenClaw Cron 系统

如果 OpenClaw 使用内置 cron 系统，在 `~/.openclaw/crons/` 创建：

**context-hub-ingest.cron**:
```
SCHEDULE=0 2 * * *
COMMAND=cd /path/to/context-hub && python3 ingest.py
DESCRIPTION=Context Hub - 自动摄入 agent memory
```

**context-hub-forget.cron**:
```
SCHEDULE=30 2 * * *
COMMAND=cd /path/to/context-hub && python3 hub.py forget
DESCRIPTION=Context Hub - 遗忘清理
```

#### 方式 B: 系统 crontab

```bash
# 编辑 crontab
crontab -e

# 添加以下行
0 2 * * * cd /path/to/context-hub && /usr/bin/python3 ingest.py >> /tmp/context-hub-ingest.log 2>&1
30 2 * * * cd /path/to/context-hub && /usr/bin/python3 hub.py forget >> /tmp/context-hub-forget.log 2>&1
```

## 验证安装

### 1. 检查数据库

```bash
python3 hub.py status
```

应该输出类似：
```
═══ Context Hub 状态 ═══

  🧠 短期记忆: 0  |  💎 长期记忆: 0
  👤 实体: 0  |  🔗 关系: 0  |  📐 向量: 0
  📝 Memos: 0  |  📊 Agent 活动: 0
```

### 2. 检查 MCP Server

```bash
.venv/bin/python mcp_server.py
```

如果没有错误输出，说明 MCP Server 可以正常启动。

### 3. 在 Agent 中测试

重启 OpenClaw Gateway 后，在任意 agent 中：

```
调用 ctx_status() 查看状态
```

如果返回 Context Hub 状态信息，说明集成成功。

## 配置说明

### 数据库路径

默认数据库路径：`~/.openclaw/workspace-knowledge-keeper/context-hub/hub.db`

如需修改，编辑 `hub.py` 中的 `DB_PATH` 变量。

### Ollama 地址

默认：`http://localhost:11434`

如需修改，编辑 `hub.py` 中的 `OLLAMA_URL` 变量。

### 嵌入模型

默认使用 `bge-m3`（1024 维向量）。

如需更换，修改 `hub.py` 中的：
- `EMBED_MODEL` - 模型名称
- `EMBED_DIM` - 向量维度

### LLM 模型（自动摄入）

自动摄入时用于信息提取的 LLM，按优先级尝试：
1. glm4
2. qwen2.5
3. llama3

可在 `ingest.py` 中的 `LLM_MODELS` 列表修改。

## 卸载

### 1. 移除 MCP 配置

编辑 `~/.openclaw/mcpServers.json`，删除 `context-hub` 条目。

### 2. 移除 Cron Jobs

删除 `~/.openclaw/crons/context-hub-*.cron` 或编辑系统 crontab。

### 3. 删除文件（可选）

```bash
rm -rf /path/to/context-hub
rm -f ~/.openclaw/workspace-knowledge-keeper/context-hub/hub.db
```

### 4. 重启 Gateway

```bash
openclaw gateway restart
```

## 故障排除

### Ollama 连接失败

```bash
# 检查 Ollama 是否运行
curl localhost:11434

# 启动 Ollama
ollama serve

# 拉取模型
ollama pull bge-m3
```

### MCP Server 无法启动

```bash
# 检查虚拟环境
ls -la .venv/bin/python

# 重新安装依赖
source .venv/bin/activate
pip install mcp
```

### 嵌入失败

```bash
# 测试 Ollama embedding
curl -X POST http://localhost:11434/api/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model": "bge-m3", "prompt": "test"}'
```

### 搜索无结果

1. 确认数据库中有数据：`python3 hub.py status`
2. 检查 FTS 索引是否正常
3. 尝试不同的搜索模式：`--mode fts` / `--mode vector` / `--mode hybrid`

## 相关文档

- [主 README](README.md) - 功能概述和基本使用
- [hub.py](hub.py) - 核心模块
- [mcp_server.py](mcp_server.py) - MCP 工具定义
- [ingest.py](ingest.py) - 自动摄入脚本
