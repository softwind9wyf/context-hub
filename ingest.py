#!/usr/bin/env python3
"""Context Hub Ingestion Pipeline
直接从各 agent 的 memory 文件中按 section 切分并建立索引。
不再调用 LLM，memory 文件本身就是 agent 提炼过的内容。
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

import jieba

# 静默 jieba 日志
jieba.setLogLevel(jieba.logging.INFO)

# ══════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════

OPENCLAW_ROOT = Path.home() / ".openclaw" / "agents"
DB_PATH = Path.home() / ".openclaw" / "workspace-knowledge-keeper" / "context-hub" / "hub.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "bge-m3"

# ══════════════════════════════════════════════
# Database
# ══════════════════════════════════════════════

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def ensure_tables():
    """确保所有需要的表存在"""
    conn = get_db()
    conn.executescript("""
        -- Memory Sources: agent memory 文件的原始条目
        CREATE TABLE IF NOT EXISTS memory_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            source_file TEXT NOT NULL,
            block_title TEXT NOT NULL,
            block_content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            section_date TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        -- FTS5 索引
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_sources_fts USING fts5(
            agent_name, block_title, block_content, segmented,
            tokenize='unicode61'
        );

        -- Ingestion 日志
        CREATE TABLE IF NOT EXISTS ingestion_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_mtime TEXT NOT NULL,
            processed_at TEXT DEFAULT (datetime('now', 'localtime')),
            status TEXT DEFAULT 'success',
            items_extracted INTEGER DEFAULT 0,
            error_message TEXT DEFAULT ''
        );
    """)

    # 确保 embeddings 表支持 'memory' scope
    try:
        conn.execute("ALTER TABLE embeddings DROP CONSTRAINT embeddings_scope_check")
    except:
        pass
    # 重建 embeddings 表（如果需要）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embeddings_new (
            scope TEXT NOT NULL CHECK(scope IN ('short', 'long', 'memory')),
            mem_id INTEGER NOT NULL,
            vector BLOB NOT NULL,
            PRIMARY KEY (scope, mem_id)
        )
    """)
    # 迁移数据
    conn.execute("""
        INSERT OR IGNORE INTO embeddings_new (scope, mem_id, vector)
        SELECT scope, mem_id, vector FROM embeddings
    """)
    conn.execute("DROP TABLE IF EXISTS embeddings")
    conn.execute("ALTER TABLE embeddings_new RENAME TO embeddings")

    conn.commit()
    conn.close()

# ══════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════

def segment(text: str) -> str:
    """中文分词：jieba 分词后用空格连接"""
    if not text:
        return ""
    words = jieba.cut_for_search(text)
    return " ".join(w.strip() for w in words if w.strip())

def compute_hash(content: str) -> str:
    """计算内容 hash"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

def parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 markdown frontmatter"""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1].strip()
            body = parts[2].strip()
            fm = {}
            for line in fm_text.split("\n"):
                if ":" in line:
                    key, val = line.split(":", 1)
                    fm[key.strip()] = val.strip()
            return fm, body
    return {}, content

def extract_date_from_frontmatter(fm: dict) -> str:
    """从 frontmatter 提取日期"""
    for key in ["date", "created", "created_at", "updated", "updated_at"]:
        if key in fm:
            val = fm[key]
            # 尝试提取 YYYY-MM-DD
            match = re.search(r"(\d{4}-\d{2}-\d{2})", val)
            if match:
                return match.group(1)
    return ""

def split_into_blocks(content: str) -> list[dict]:
    """按 ## 标题切分成独立 blocks"""
    blocks = []

    # 先解析 frontmatter
    fm, body = parse_frontmatter(content)
    section_date = extract_date_from_frontmatter(fm)

    # 按 ## 切分
    lines = body.split("\n")
    current_title = ""
    current_content_lines = []

    for line in lines:
        if line.startswith("## "):
            # 保存前一个 block
            if current_title or current_content_lines:
                block_content = "\n".join(current_content_lines).strip()
                if block_content:
                    blocks.append({
                        "title": current_title,
                        "content": block_content,
                        "section_date": section_date
                    })
            # 开始新 block
            current_title = line[3:].strip()
            current_content_lines = []
        else:
            current_content_lines.append(line)

    # 处理最后一个 block
    if current_title or current_content_lines:
        block_content = "\n".join(current_content_lines).strip()
        if block_content:
            blocks.append({
                "title": current_title,
                "content": block_content,
                "section_date": section_date
            })

    # 如果没有 ## 标题，整个内容作为一个 block
    if not blocks and body.strip():
        # 用第一句话作为标题（截取前 30 字符）
        first_line = body.strip().split("\n")[0]
        title = first_line[:30]
        if len(first_line) > 30:
            title += "..."
        blocks.append({
            "title": title,
            "content": body.strip(),
            "section_date": section_date
        })

    return blocks

# ══════════════════════════════════════════════
# Embedding
# ══════════════════════════════════════════════

def get_embedding(text: str) -> list[float]:
    """调用 Ollama embedding API"""
    payload = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode()
    req = urllib.request.Request(
        OLLAMA_EMBED_URL,
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["embedding"]

def check_ollama_embed():
    """检查 Ollama embedding 服务是否可用"""
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            info = json.loads(resp.read())
            available = [m["name"].split(":")[0] for m in info.get("models", [])]
            return EMBED_MODEL in available or any(EMBED_MODEL in m for m in available)
    except Exception:
        return False

# ══════════════════════════════════════════════
# Ingestion Log
# ══════════════════════════════════════════════

def get_last_processed(agent_name: str, file_path: str) -> str | None:
    """获取上次处理记录的 mtime"""
    conn = get_db()
    row = conn.execute(
        "SELECT file_mtime FROM ingestion_log WHERE agent_name=? AND file_path=? ORDER BY processed_at DESC LIMIT 1",
        (agent_name, file_path)
    ).fetchone()
    conn.close()
    return row["file_mtime"] if row else None

def log_ingestion(agent_name: str, file_path: str, file_mtime: str, status: str = "success", items: int = 0, error: str = ""):
    """记录处理日志"""
    conn = get_db()
    conn.execute(
        """INSERT INTO ingestion_log (agent_name, file_path, file_mtime, status, items_extracted, error_message)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (agent_name, file_path, file_mtime, status, items, error)
    )
    conn.commit()
    conn.close()

# ══════════════════════════════════════════════
# Memory Indexing
# ══════════════════════════════════════════════

def is_block_indexed(agent_name: str, source_file: str, content_hash: str) -> bool:
    """检查 block 是否已索引"""
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM memory_sources WHERE agent_name=? AND source_file=? AND content_hash=?",
        (agent_name, source_file, content_hash)
    ).fetchone()
    conn.close()
    return row is not None

def index_block(agent_name: str, source_file: str, block: dict, dry_run: bool = False) -> bool:
    """索引单个 block"""
    content_hash = compute_hash(block["content"])

    # 去重检查
    if is_block_indexed(agent_name, source_file, content_hash):
        return False  # 已存在

    if dry_run:
        return True

    conn = get_db()

    # 写入 memory_sources
    cur = conn.execute(
        """INSERT INTO memory_sources (agent_name, source_file, block_title, block_content, content_hash, section_date)
           VALUES (?, ?, ?, ?, ?, ?) RETURNING id""",
        (agent_name, source_file, block["title"], block["content"], content_hash, block["section_date"])
    )
    mem_id = cur.fetchone()[0]

    # 写入 FTS
    seg = segment(f"{block['title']} {block['content']}")
    conn.execute(
        "INSERT INTO memory_sources_fts(rowid, agent_name, block_title, block_content, segmented) VALUES (?, ?, ?, ?, ?)",
        (mem_id, agent_name, block["title"], block["content"][:500], seg)
    )

    conn.commit()
    conn.close()

    # 异步 embedding
    _embed_mem(mem_id, f"{block['title']}. {block['content'][:300]}")

    return True

def _embed_mem(mem_id: int, text: str):
    """写入 embedding"""
    try:
        vector = get_embedding(text)
        import struct
        blob = struct.pack(f"{len(vector)}f", *vector)
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO embeddings (scope, mem_id, vector) VALUES (?, ?, ?)",
            ("memory", mem_id, blob)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  ⚠️ embedding 失败 [memory:{mem_id}]: {e}", file=sys.stderr)

# ══════════════════════════════════════════════
# File Discovery
# ══════════════════════════════════════════════

def discover_agents() -> list[str]:
    """发现所有 agent：从 workspace-{agent-name} 目录推导"""
    agents = []
    openclaw_home = Path.home() / ".openclaw"
    for item in openclaw_home.iterdir():
        if item.is_dir() and item.name.startswith("workspace-"):
            # workspace-knowledge-keeper → knowledge-keeper
            agent = item.name[len("workspace-"):]
            agents.append(agent)
    return sorted(agents)

def discover_memory_files(agent_name: str) -> list[Path]:
    """发现 agent 的 memory 文件"""
    files = []
    workspace = Path.home() / ".openclaw" / f"workspace-{agent_name}"

    if not workspace.exists():
        return files

    # MEMORY.md
    memory_file = workspace / "MEMORY.md"
    if memory_file.exists():
        files.append(memory_file)

    # memory/*.md
    memory_dir = workspace / "memory"
    if memory_dir.exists():
        for md_file in memory_dir.glob("*.md"):
            files.append(md_file)

    return sorted(files)

# ══════════════════════════════════════════════
# File Processing
# ══════════════════════════════════════════════

def process_file(file_path: Path, agent_name: str, dry_run: bool = False, force: bool = False) -> dict:
    """处理单个 memory 文件"""
    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    source_file = file_path.name

    # 变更检测
    if not force:
        last_mtime = get_last_processed(agent_name, str(file_path))
        if last_mtime and last_mtime >= file_mtime:
            return {"status": "skipped", "reason": "未变更"}

    # 读取文件
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return {"status": "error", "reason": str(e)}

    if not content.strip():
        return {"status": "skipped", "reason": "空文件"}

    print(f"\n  📄 {source_file} ({len(content)} 字符)")

    # 按 section 切分
    blocks = split_into_blocks(content)

    if dry_run:
        print(f"     [DRY-RUN] 将索引 {len(blocks)} 个 blocks:")
        for b in blocks:
            print(f"       - {b['title'][:50]}")
        return {"status": "dry_run", "items": len(blocks)}

    # 索引每个 block
    items_added = 0
    for block in blocks:
        try:
            if index_block(agent_name, source_file, block, dry_run=False):
                items_added += 1
                print(f"     📝 {block['title'][:50]}")
        except Exception as e:
            print(f"     ⚠️ 索引失败: {block['title'][:30]}: {e}")

    # 记录日志
    log_ingestion(agent_name, str(file_path), file_mtime, "success", items_added)

    return {"status": "success", "items": items_added}

# ══════════════════════════════════════════════
# Main Entry
# ══════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Context Hub 自动摄入 Pipeline（直接索引模式）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 ingest.py                    # 处理所有 agent
  python3 ingest.py --agent knowledge-keeper  # 只处理指定 agent
  python3 ingest.py --dry-run          # 预览模式
  python3 ingest.py --force            # 强制重新索引（忽略 mtime）
"""
    )
    parser.add_argument("--agent", "-a", help="指定要处理的 agent 名称")
    parser.add_argument("--dry-run", "-n", action="store_true", help="预览模式，不实际写入")
    parser.add_argument("--force", "-f", action="store_true", help="强制重新索引（忽略 mtime）")
    args = parser.parse_args()

    # 检查 Ollama embedding 服务
    print("🔍 检查 Ollama embedding 服务...")
    if not check_ollama_embed():
        print(f"❌ Ollama embedding 服务未就绪，请确保 bge-m3 模型可用:")
        print(f"   ollama pull bge-m3")
        sys.exit(1)
    print(f"✅ embedding 模型可用: {EMBED_MODEL}")

    # 初始化数据库
    ensure_tables()

    # 确定要处理的 agent
    if args.agent:
        agents = [args.agent]
    else:
        agents = discover_agents()

    if not agents:
        print("❌ 未找到任何 agent")
        sys.exit(1)

    # 统计
    stats = {
        "agents_scanned": 0,
        "files_processed": 0,
        "blocks_indexed": 0,
        "skipped": 0,
        "errors": 0
    }

    print(f"\n📋 开始扫描 {len(agents)} 个 agent...\n")
    print("=" * 50)

    for agent_name in agents:
        files = discover_memory_files(agent_name)
        if not files:
            continue

        stats["agents_scanned"] += 1
        print(f"\n🤖 {agent_name}")

        for file_path in files:
            result = process_file(file_path, agent_name, args.dry_run, args.force)

            if result["status"] == "success":
                stats["files_processed"] += 1
                stats["blocks_indexed"] += result.get("items", 0)
            elif result["status"] == "skipped":
                stats["skipped"] += 1
                print(f"  ⏭️ {file_path.name}: {result['reason']}")
            elif result["status"] == "error":
                stats["errors"] += 1
                print(f"  ❌ {file_path.name}: {result['reason']}")
            elif result["status"] == "dry_run":
                stats["files_processed"] += 1
                stats["blocks_indexed"] += result.get("items", 0)

    # 输出报告
    print("\n" + "=" * 50)
    print("\n📊 处理报告:")
    print(f"   扫描 agent: {stats['agents_scanned']}")
    print(f"   处理文件: {stats['files_processed']}")
    print(f"   新索引 blocks: {stats['blocks_indexed']}")
    print(f"   跳过文件: {stats['skipped']}")
    print(f"   错误: {stats['errors']}")

    if args.dry_run:
        print("\n  [DRY-RUN 模式] 未实际写入数据库")

    print("\n✅ 完成")

if __name__ == "__main__":
    main()
