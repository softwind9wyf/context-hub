#!/bin/bash
# Context Hub Setup Script
# 一键安装 Context Hub 到 OpenClaw

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 图标
CHECK="${GREEN}✅${NC}"
CROSS="${RED}❌${NC}"
WARN="${YELLOW}⚠️${NC}"
INFO="${BLUE}ℹ️${NC}"

# 获取脚本所在目录的绝对路径
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTEXT_HUB_DIR="$SCRIPT_DIR"

echo ""
echo "═══════════════════════════════════════════════════════"
echo "       Context Hub Setup - OpenClaw 集成安装"
echo "═══════════════════════════════════════════════════════"
echo ""

# ══════════════════════════════════════════════
# Step 1: 环境检查
# ══════════════════════════════════════════════
echo -e "${INFO} Step 1: 环境检查"
echo ""

# 检查 Python 3.10+
check_python() {
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
        PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
        PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

        if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 10 ]; then
            echo -e "  ${CHECK} Python $PYTHON_VERSION"
            return 0
        else
            echo -e "  ${CROSS} Python 版本过低: $PYTHON_VERSION (需要 3.10+)"
            echo -e "     ${WARN} 请升级 Python: brew install python@3.12"
            return 1
        fi
    else
        echo -e "  ${CROSS} 未找到 Python 3"
        echo -e "     ${WARN} 请安装 Python 3: brew install python@3.12"
        return 1
    fi
}

# 检查 Ollama 是否运行
check_ollama() {
    if curl -s localhost:11434 > /dev/null 2>&1; then
        echo -e "  ${CHECK} Ollama 服务运行中"
        # 检查 bge-m3 模型
        if curl -s localhost:11434/api/tags | grep -q "bge-m3"; then
            echo -e "  ${CHECK} bge-m3 模型已安装"
            return 0
        else
            echo -e "  ${WARN} bge-m3 模型未安装"
            echo -e "     ${INFO} 正在安装 bge-m3 模型..."
            ollama pull bge-m3 2>/dev/null || {
                echo -e "     ${WARN} 自动安装失败，请手动运行: ollama pull bge-m3"
            }
            return 0
        fi
    else
        echo -e "  ${CROSS} Ollama 未运行"
        echo -e "     ${WARN} 请先启动 Ollama: ollama serve"
        return 1
    fi
}

# 检查 jieba
check_jieba() {
    if python3 -c "import jieba" 2>/dev/null; then
        echo -e "  ${CHECK} jieba 已安装"
        return 0
    else
        echo -e "  ${WARN} jieba 未安装"
        echo -e "     ${INFO} 正在安装 jieba..."
        pip3 install jieba 2>/dev/null || pip install jieba 2>/dev/null || {
            echo -e "     ${CROSS} jieba 安装失败，请手动运行: pip3 install jieba"
            return 1
        }
        echo -e "  ${CHECK} jieba 安装成功"
        return 0
    fi
}

# 执行环境检查
ENV_OK=true
check_python || ENV_OK=false
check_ollama || ENV_OK=false
check_jieba || ENV_OK=false

if [ "$ENV_OK" = false ]; then
    echo ""
    echo -e "${CROSS} 环境检查失败，请先解决上述问题后重试"
    exit 1
fi

echo ""

# ══════════════════════════════════════════════
# Step 2: 初始化数据库
# ══════════════════════════════════════════════
echo -e "${INFO} Step 2: 初始化数据库"
echo ""

cd "$CONTEXT_HUB_DIR"
python3 hub.py init
echo ""

# ══════════════════════════════════════════════
# Step 3: 安装 MCP 依赖
# ══════════════════════════════════════════════
echo -e "${INFO} Step 3: 安装 MCP 依赖"
echo ""

if [ ! -d "$CONTEXT_HUB_DIR/.venv" ]; then
    echo -e "  ${INFO} 创建 Python 虚拟环境..."
    python3 -m venv "$CONTEXT_HUB_DIR/.venv"
fi

echo -e "  ${INFO} 安装 mcp 包..."
source "$CONTEXT_HUB_DIR/.venv/bin/activate"
pip install mcp -q
deactivate

echo -e "  ${CHECK} MCP 依赖安装完成"
echo ""

# ══════════════════════════════════════════════
# Step 4: 注册 MCP Server
# ══════════════════════════════════════════════
echo -e "${INFO} Step 4: 注册 MCP Server"
echo ""

# 查找 OpenClaw MCP 配置文件
OPENCLAW_DIR="$HOME/.openclaw"
MCP_CONFIG=""

# 可能的配置文件位置
CONFIG_PATHS=(
    "$OPENCLAW_DIR/gateway/mcpServers.json"
    "$OPENCLAW_DIR/mcpServers.json"
    "$OPENCLAW_DIR/config/mcpServers.json"
)

for path in "${CONFIG_PATHS[@]}"; do
    if [ -f "$path" ]; then
        MCP_CONFIG="$path"
        break
    fi
done

# 如果没找到，使用默认位置
if [ -z "$MCP_CONFIG" ]; then
    MCP_CONFIG="$OPENCLAW_DIR/mcpServers.json"
    mkdir -p "$OPENCLAW_DIR"
    echo "{}" > "$MCP_CONFIG"
fi

echo -e "  ${INFO} MCP 配置文件: $MCP_CONFIG"

# 读取现有配置
if [ -f "$MCP_CONFIG" ]; then
    EXISTING_CONFIG=$(cat "$MCP_CONFIG")
else
    EXISTING_CONFIG="{}"
fi

# 创建新的 context-hub 配置
VENV_PYTHON="$CONTEXT_HUB_DIR/.venv/bin/python"
MCP_SERVER_PATH="$CONTEXT_HUB_DIR/mcp_server.py"

# 使用 Python 更新 JSON（更可靠）
python3 << EOF
import json
import sys

config_path = "$MCP_CONFIG"
new_server = {
    "context-hub": {
        "command": "$VENV_PYTHON",
        "args": ["$MCP_SERVER_PATH"],
        "type": "stdio"
    }
}

try:
    with open(config_path, 'r') as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}

config.update(new_server)

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)

print("  ${CHECK} MCP Server 配置已添加")
EOF

echo ""

# ══════════════════════════════════════════════
# Step 5: 创建 OpenClaw Cron Jobs
# ══════════════════════════════════════════════
echo -e "${INFO} Step 5: 创建 OpenClaw Cron Jobs"
echo ""

# 检查 openclaw CLI 是否可用
if command -v openclaw &> /dev/null; then
    echo -e "  ${INFO} 使用 OpenClaw cron 系统"

    # 创建 setup-crons.py 临时脚本来注册 cron jobs
    SETUP_CRONS=$(mktemp /tmp/context-hub-crons-XXXXXX.py)
    cat > "$SETUP_CRONS" << 'PYEOF'
import subprocess, json, sys

def cron_add(name, schedule_expr, message):
    """通过 openclaw gateway API 添加 cron job"""
    payload = json.dumps({
        "name": name,
        "schedule": {"kind": "cron", "expr": schedule_expr, "tz": "Asia/Shanghai"},
        "payload": {"kind": "agentTurn", "message": message},
        "sessionTarget": "isolated",
        "enabled": True
    })
    result = subprocess.run(
        ["openclaw", "cron", "add"],
        input=payload, capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"  ✅ Cron job '{name}' 已创建 ({schedule_expr})")
    else:
        print(f"  ⚠️  Cron job '{name}' 创建失败: {result.stderr.strip()}")
        print(f"      可手动添加，请参考 README_SETUP.md")

cron_add(
    "context-hub-ingest",
    "0 2 * * *",
    "执行 Context Hub 摄入任务：cd $CONTEXT_HUB_DIR && python3 ingest.py，扫描所有 agent 的 memory 文件，对新内容建立 FTS 和向量索引。完成后用 ctx_status 确认状态。"
)

cron_add(
    "context-hub-forget",
    "30 2 * * *",
    "执行 Context Hub 遗忘清理：cd $CONTEXT_HUB_DIR && python3 hub.py forget，清理过期短期记忆，衰减长期记忆权重。完成后报告清理数量。"
)
PYEOF

    # 替换路径变量
    sed -i '' "s|\$CONTEXT_HUB_DIR|$CONTEXT_HUB_DIR|g" "$SETUP_CRONS"
    python3 "$SETUP_CRONS"
    rm -f "$SETUP_CRONS"
else
    echo -e "  ${WARN} 未找到 openclaw CLI，跳过 cron 注册"
    echo -e "     ${INFO} 安装后请手动运行以下命令注册 cron："
    echo ""
    echo '     openclaw cron add '"'"'{"name":"context-hub-ingest","schedule":{"kind":"cron","expr":"0 2 * * *","tz":"Asia/Shanghai"},"payload":{"kind":"agentTurn","message":"执行 context-hub ingest：扫描所有 agent memory 文件并建立索引"},"sessionTarget":"isolated"}'"'"
    echo ""
    echo '     openclaw cron add '"'"'{"name":"context-hub-forget","schedule":{"kind":"cron","expr":"30 2 * * *","tz":"Asia/Shanghai"},"payload":{"kind":"agentTurn","message":"执行 context-hub forget：清理过期记忆和衰减权重"},"sessionTarget":"isolated"}'"'"
fi

echo ""

# ══════════════════════════════════════════════
# Step 6: 创建 Heartbeats 目录
# ══════════════════════════════════════════════
echo -e "${INFO} Step 6: 创建 Heartbeats 模板"
echo ""

HEARTBEATS_DIR="$CONTEXT_HUB_DIR/heartbeats"
mkdir -p "$HEARTBEATS_DIR"

if [ -f "$HEARTBEATS_DIR/context-hub-report.md" ]; then
    echo -e "  ${WARN} heartbeats/context-hub-report.md 已存在"
else
    cat > "$HEARTBEATS_DIR/context-hub-report.md" << 'EOF'
# Context Hub Heartbeat 上报

如果有值得共享的信息，通过 MCP 工具上报：
- ctx_activity_report: 关键决策、任务完成、重要发现
- ctx_memo_add: 跨 agent 共享的笔记和洞察
- ctx_entity_add: 新发现的人物、项目、工具、组织

不要重复上报已知信息。只在有新发现时上报。
EOF
    echo -e "  ${CHECK} 创建 heartbeats/context-hub-report.md"
fi

echo ""

# ══════════════════════════════════════════════
# Step 7: 输出摘要
# ══════════════════════════════════════════════
echo "═══════════════════════════════════════════════════════"
echo -e "${GREEN}✅ Context Hub 安装完成！${NC}"
echo "═══════════════════════════════════════════════════════"
echo ""
echo -e "${INFO} 安装信息:"
echo "  • 安装目录: $CONTEXT_HUB_DIR"
echo "  • 数据库: $HOME/.openclaw/workspace-knowledge-keeper/context-hub/hub.db"
echo "  • MCP 配置: $MCP_CONFIG"
echo ""
echo -e "${INFO} 后续步骤:"
echo "  1. 重启 OpenClaw Gateway 以加载新的 MCP Server"
echo "     openclaw gateway restart"
echo ""
echo "  2. 验证 MCP 工具是否可用"
echo "     在 agent 中调用 ctx_status() 应返回 Context Hub 状态"
echo ""
echo "  3. 手动触发摄入（可选）"
echo "     cd $CONTEXT_HUB_DIR && python3 ingest.py"
echo ""
echo -e "${INFO} MCP 工具列表:"
echo "  • ctx_recall        - 统一检索（关键词/语义/混合）"
echo "  • ctx_short_add     - 添加短期记忆"
echo "  • ctx_long_add      - 添加长期记忆"
echo "  • ctx_memo_add      - 添加跨 agent 共享笔记"
echo "  • ctx_activity_report - 上报 agent 活动"
echo "  • ctx_entity_add    - 添加实体（人物/项目/工具等）"
echo "  • ctx_rel_add       - 添加实体关系"
echo "  • ctx_graph         - 查询实体关系图"
echo "  • ctx_status        - 查看系统状态"
echo "  • ctx_forget        - 执行遗忘清理"
echo "  • ctx_consolidate   - 查看整合候选"
echo ""
echo -e "${INFO} 自动任务:"
echo "  • 每天 02:00 - 自动摄入各 agent 的 memory 文件"
echo "  • 每天 02:30 - 清理过期记忆和衰减权重"
echo ""
echo -e "${GREEN}🎉 开始使用 Context Hub 管理你的 AI 记忆吧！${NC}"
echo ""
