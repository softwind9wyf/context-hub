#!/usr/bin/env python3
"""Context Hub - 统一记忆系统
基于 SQLite + 向量检索，模拟人类短期/长期记忆模型。
"""

import sqlite3
import sys
import json
import math
import struct
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta
import jieba

# 首次加载 jieba 静默模式
jieba.setLogLevel(jieba.logging.INFO)

def segment(text: str) -> str:
    """中文分词：jieba 分词后用空格连接"""
    if not text:
        return ""
    words = jieba.cut_for_search(text)
    return " ".join(w.strip() for w in words if w.strip())

DB_PATH = Path.home() / ".openclaw" / "workspace-knowledge-keeper" / "context-hub" / "hub.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

OLLAMA_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "bge-m3"
EMBED_DIM = 1024

# ══════════════════════════════════════════════
# Embedding
# ══════════════════════════════════════════════

def get_embedding(text: str) -> list[float]:
    payload = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["embedding"]

def vec_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)

def blob_to_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))

def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)

# ══════════════════════════════════════════════
# Database
# ══════════════════════════════════════════════

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        -- 短期记忆：最近的事件、对话摘要、待办
        CREATE TABLE IF NOT EXISTS short_term (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mem_type TEXT NOT NULL CHECK(mem_type IN ('event', 'conversation', 'todo', 'decision', 'note')),
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            importance REAL DEFAULT 0.5,
            access_count INTEGER DEFAULT 0,
            last_accessed TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            expires_at TEXT
        );

        -- 长期记忆：持久化的知识、经验、人物、项目
        CREATE TABLE IF NOT EXISTS long_term (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mem_type TEXT NOT NULL CHECK(mem_type IN ('fact', 'person', 'project', 'experience', 'preference', 'knowledge', 'relation')),
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            importance REAL DEFAULT 0.5,
            confidence REAL DEFAULT 1.0,
            access_count INTEGER DEFAULT 0,
            last_accessed TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime')),
            verified_at TEXT
        );

        -- 实体：人、项目、组织等
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            entity_type TEXT NOT NULL CHECK(entity_type IN ('person', 'project', 'org', 'tool', 'concept', 'location')),
            aliases TEXT DEFAULT '',
            description TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        -- 关系：实体之间的连接
        CREATE TABLE IF NOT EXISTS relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_entity INTEGER NOT NULL REFERENCES entities(id),
            to_entity INTEGER NOT NULL REFERENCES entities(id),
            rel_type TEXT NOT NULL,
            description TEXT DEFAULT '',
            since TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(from_entity, to_entity, rel_type)
        );

        -- FTS 索引（独立表，segmented 列存 jieba 分词结果）
        CREATE VIRTUAL TABLE IF NOT EXISTS short_fts USING fts5(
            title, content, segmented,
            tokenize='unicode61'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS long_fts USING fts5(
            title, content, segmented,
            tokenize='unicode61'
        );

        -- 向量表
        CREATE TABLE IF NOT EXISTS embeddings (
            scope TEXT NOT NULL CHECK(scope IN ('short', 'long', 'memory')),
            mem_id INTEGER NOT NULL,
            vector BLOB NOT NULL,
            PRIMARY KEY (scope, mem_id)
        );

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

        -- Memory Sources FTS5 索引
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_sources_fts USING fts5(
            agent_name, block_title, block_content, segmented,
            tokenize='unicode61'
        );

        -- 整合日志：记录哪些短期记忆已整合到长期
        CREATE TABLE IF NOT EXISTS consolidation_log (
            short_id INTEGER REFERENCES short_term(id),
            long_id INTEGER REFERENCES long_term(id),
            consolidated_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        -- Agent 活动流：记录各 agent 的活动
        CREATE TABLE IF NOT EXISTS agent_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            activity_type TEXT NOT NULL CHECK(activity_type IN ('task_completed', 'decision_made', 'info_reported', 'error_occurred', 'heartbeat_report')),
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            session_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        -- 跨 agent 共享笔记
        CREATE TABLE IF NOT EXISTS memos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            memo_type TEXT NOT NULL CHECK(memo_type IN ('fact', 'insight', 'question', 'answer', 'cross_reference')),
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT DEFAULT '',
            importance REAL DEFAULT 0.5,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            expires_at TEXT,
            related_entities TEXT DEFAULT ''
        );

        -- FTS v2 索引（支持 agent_name 搜索）
        CREATE VIRTUAL TABLE IF NOT EXISTS short_fts_v2 USING fts5(
            title, content, segmented, agent_name,
            tokenize='unicode61'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS long_fts_v2 USING fts5(
            title, content, segmented, agent_name,
            tokenize='unicode61'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS memos_fts USING fts5(
            title, content, segmented, agent_name, tags,
            tokenize='unicode61'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS activity_fts USING fts5(
            title, content, agent_name,
            tokenize='unicode61'
        );
    """)

    # 为现有表添加 agent_name 字段（兼容性处理）
    try:
        conn.execute("ALTER TABLE short_term ADD COLUMN agent_name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE long_term ADD COLUMN agent_name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # 列已存在

    conn.commit()
    conn.close()
    print(f"✅ Context Hub 初始化完成: {DB_PATH}")

# ══════════════════════════════════════════════
# FTS helpers (jieba 分词)
# ══════════════════════════════════════════════

def _fts_insert(conn, table, mem_id, title, content):
    """向 FTS 表插入分词后的记录"""
    seg = segment(f"{title} {content}")
    conn.execute(
        f"INSERT INTO {table}_fts(rowid, title, content, segmented) VALUES (?, ?, ?, ?)",
        (mem_id, title, content[:500], seg)
    )

def _fts_delete(conn, table, mem_id):
    """从 FTS 表删除记录"""
    conn.execute(
        f"INSERT INTO {table}_fts({table}_fts, rowid) VALUES ('delete', ?)",
        (mem_id,)
    )

def _fts_update(conn, table, mem_id, title, content):
    """更新 FTS 表记录"""
    _fts_delete(conn, table, mem_id)
    _fts_insert(conn, table, mem_id, title, content)

# ══════════════════════════════════════════════
# Short-term Memory
# ══════════════════════════════════════════════

def short_add(mem_type, title, content, source="", tags="", importance=0.5, expire_days=None):
    """添加短期记忆"""
    conn = get_db()
    expires = None
    if expire_days:
        expires = (datetime.now() + timedelta(days=expire_days)).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        """INSERT INTO short_term (mem_type, title, content, source, tags, importance, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id""",
        (mem_type, title, content, source, tags, importance, expires)
    )
    sid = cur.fetchone()[0]
    conn.commit()
    _fts_insert(conn, "short", sid, title, content)
    conn.commit()
    conn.close()

    # 异步 embedding
    _embed_mem("short", sid, f"{title}. {content[:300]}")
    return sid

def short_list(mem_type=None, limit=20):
    """列出短期记忆"""
    conn = get_db()
    if mem_type:
        rows = conn.execute(
            "SELECT * FROM short_term WHERE mem_type=? ORDER BY created_at DESC LIMIT ?",
            (mem_type, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM short_term ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return rows

def short_get(mem_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM short_term WHERE id=?", (mem_id,)).fetchone()
    conn.close()
    return row

def short_delete(mem_id):
    conn = get_db()
    _fts_delete(conn, "short", mem_id)
    conn.execute("DELETE FROM short_term WHERE id=?", (mem_id,))
    conn.execute("DELETE FROM embeddings WHERE scope='short' AND mem_id=?", (mem_id,))
    conn.commit()
    conn.close()

# ══════════════════════════════════════════════
# Long-term Memory
# ══════════════════════════════════════════════

def long_add(mem_type, title, content, source="", tags="", importance=0.5, confidence=1.0):
    """添加长期记忆"""
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO long_term (mem_type, title, content, source, tags, importance, confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id""",
        (mem_type, title, content, source, tags, importance, confidence)
    )
    lid = cur.fetchone()[0]
    conn.commit()
    _fts_insert(conn, "long", lid, title, content)
    conn.commit()
    conn.close()

    _embed_mem("long", lid, f"{title}. {content[:300]}")
    return lid

def long_update(mem_id, content=None, importance=None, confidence=None):
    """更新长期记忆"""
    conn = get_db()
    sets = ["updated_at=datetime('now', 'localtime')"]
    params = []
    if content is not None:
        sets.append("content=?")
        params.append(content)
    if importance is not None:
        sets.append("importance=?")
        params.append(importance)
    if confidence is not None:
        sets.append("confidence=?")
        params.append(confidence)
    params.append(mem_id)
    conn.execute(f"UPDATE long_term SET {', '.join(sets)} WHERE id=?", params)
    # 如果更新了 content，同步 FTS
    if content is not None:
        row = conn.execute("SELECT title, content FROM long_term WHERE id=?", (mem_id,)).fetchone()
        if row:
            _fts_update(conn, "long", mem_id, row["title"], content)
    conn.commit()
    conn.close()

def long_list(mem_type=None, limit=20):
    conn = get_db()
    if mem_type:
        rows = conn.execute(
            "SELECT * FROM long_term WHERE mem_type=? ORDER BY updated_at DESC LIMIT ?",
            (mem_type, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM long_term ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return rows

def long_get(mem_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM long_term WHERE id=?", (mem_id,)).fetchone()
    conn.close()
    return row

def long_delete(mem_id):
    conn = get_db()
    _fts_delete(conn, "long", mem_id)
    conn.execute("DELETE FROM long_term WHERE id=?", (mem_id,))
    conn.execute("DELETE FROM embeddings WHERE scope='long' AND mem_id=?", (mem_id,))
    conn.commit()
    conn.close()

# ══════════════════════════════════════════════
# Entities & Relations
# ══════════════════════════════════════════════

def entity_add(name, entity_type, aliases="", description=""):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO entities (name, entity_type, aliases, description) VALUES (?, ?, ?, ?) RETURNING id",
            (name, entity_type, aliases, description)
        )
        row = cur.fetchone()
        conn.commit()
        eid = row[0] if row else None
        if eid is None:
            row = conn.execute("SELECT id FROM entities WHERE name=?", (name,)).fetchone()
            eid = row[0] if row else None
    finally:
        conn.close()
    return eid

def entity_find(name):
    conn = get_db()
    row = conn.execute("SELECT * FROM entities WHERE name=? OR aliases LIKE ?", (name, f"%{name}%")).fetchone()
    conn.close()
    return row

def entity_list(entity_type=None, limit=50):
    conn = get_db()
    if entity_type:
        rows = conn.execute("SELECT * FROM entities WHERE entity_type=? ORDER BY name", (entity_type, limit)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM entities ORDER BY entity_type, name LIMIT ?", (limit,)).fetchall()
    conn.close()
    return rows

def relation_add(from_name, to_name, rel_type, description=""):
    """添加关系（自动查找或创建实体）"""
    conn = get_db()
    from_id = _ensure_entity(conn, from_name)
    to_id = _ensure_entity(conn, to_name)
    if from_id and to_id:
        conn.execute(
            "INSERT OR IGNORE INTO relations (from_entity, to_entity, rel_type, description) VALUES (?, ?, ?, ?)",
            (from_id, to_id, rel_type, description)
        )
    conn.commit()
    conn.close()

def _ensure_entity(conn, name):
    row = conn.execute("SELECT id FROM entities WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    # Try aliases
    row = conn.execute("SELECT id FROM entities WHERE aliases LIKE ?", (f"%{name}%",)).fetchone()
    if row:
        return row[0]
    # Create minimal
    cur = conn.execute("INSERT OR IGNORE INTO entities (name, entity_type) VALUES (?, 'concept') RETURNING id", (name,))
    r = cur.fetchone()
    return r[0] if r else None

def entity_graph(entity_name, depth=1):
    """获取实体及其关系图"""
    conn = get_db()
    row = conn.execute("SELECT id, name, entity_type, description FROM entities WHERE name=? OR aliases LIKE ?", (entity_name, f"%{entity_name}%")).fetchone()
    if not row:
        conn.close()
        return None

    result = {"entity": dict(row), "relations": []}
    visited = {row[0]}

    # BFS
    frontier = [row[0]]
    for _ in range(depth):
        next_frontier = []
        for eid in frontier:
            rels = conn.execute("""
                SELECT r.rel_type, r.description,
                       e1.name as from_name, e1.entity_type as from_type,
                       e2.name as to_name, e2.entity_type as to_type
                FROM relations r
                JOIN entities e1 ON r.from_entity=e1.id
                JOIN entities e2 ON r.to_entity=e2.id
                WHERE r.from_entity=? OR r.to_entity=?
            """, (eid, eid)).fetchall()
            for rel in rels:
                result["relations"].append(dict(rel))
                # Find the other entity id
                if rel["from_name"] == row["name"] or rel["from_name"] in [r["from_name"] for r in result["relations"]]:
                    pass
        frontier = next_frontier

    conn.close()
    return result

# ══════════════════════════════════════════════
# Agent Activity (Agent 活动流)
# ══════════════════════════════════════════════

def activity_report(agent_name, activity_type, title, content, session_id=""):
    """记录 agent 活动到活动流"""
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO agent_activity (agent_name, activity_type, title, content, session_id)
           VALUES (?, ?, ?, ?, ?) RETURNING id""",
        (agent_name, activity_type, title, content, session_id)
    )
    aid = cur.fetchone()[0]
    # 更新 FTS
    seg = segment(f"{title} {content} {agent_name}")
    conn.execute(
        "INSERT INTO activity_fts(rowid, title, content, agent_name) VALUES (?, ?, ?, ?)",
        (aid, title, content[:500], agent_name)
    )
    conn.commit()
    conn.close()
    return aid

def activity_list(agent_name=None, limit=20):
    """列出 agent 活动"""
    conn = get_db()
    if agent_name:
        rows = conn.execute(
            "SELECT * FROM agent_activity WHERE agent_name=? ORDER BY created_at DESC LIMIT ?",
            (agent_name, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM agent_activity ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return rows

# ══════════════════════════════════════════════
# Memos (跨 agent 共享笔记)
# ══════════════════════════════════════════════

def memo_add(agent_name, memo_type, title, content, tags="", importance=0.5, expire_days=None, related_entities=""):
    """添加跨 agent 共享笔记"""
    conn = get_db()
    expires = None
    if expire_days:
        expires = (datetime.now() + timedelta(days=expire_days)).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        """INSERT INTO memos (agent_name, memo_type, title, content, tags, importance, expires_at, related_entities)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
        (agent_name, memo_type, title, content, tags, importance, expires, related_entities)
    )
    mid = cur.fetchone()[0]
    # 更新 FTS
    seg = segment(f"{title} {content} {agent_name} {tags}")
    conn.execute(
        "INSERT INTO memos_fts(rowid, title, content, segmented, agent_name, tags) VALUES (?, ?, ?, ?, ?, ?)",
        (mid, title, content[:500], seg, agent_name, tags)
    )
    conn.commit()
    conn.close()
    return mid

def memo_list(agent_name=None, memo_type=None, limit=20):
    """列出共享笔记"""
    conn = get_db()
    query = "SELECT * FROM memos WHERE 1=1"
    params = []
    if agent_name:
        query += " AND agent_name=?"
        params.append(agent_name)
    if memo_type:
        query += " AND memo_type=?"
        params.append(memo_type)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows

def memo_get(memo_id):
    """获取单条 memo"""
    conn = get_db()
    row = conn.execute("SELECT * FROM memos WHERE id=?", (memo_id,)).fetchone()
    conn.close()
    return row

def memo_delete(memo_id):
    """删除 memo"""
    conn = get_db()
    conn.execute("INSERT INTO memos_fts(memos_fts, rowid) VALUES ('delete', ?)", (memo_id,))
    conn.execute("DELETE FROM memos WHERE id=?", (memo_id,))
    conn.commit()
    conn.close()

# ══════════════════════════════════════════════
# Consolidation (短期 → 长期 整合)
# ══════════════════════════════════════════════

def consolidate_candidates(min_importance=0.7, min_age_days=1):
    """获取适合整合到长期记忆的短期记忆候选"""
    conn = get_db()
    cutoff = (datetime.now() - timedelta(days=min_age_days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute("""
        SELECT s.* FROM short_term s
        LEFT JOIN consolidation_log c ON c.short_id = s.id
        WHERE s.importance >= ?
          AND s.created_at <= ?
          AND c.short_id IS NULL
        ORDER BY s.importance DESC
    """, (min_importance, cutoff)).fetchall()
    conn.close()
    return rows

def consolidate(short_id, long_id):
    """记录整合关系"""
    conn = get_db()
    conn.execute(
        "INSERT INTO consolidation_log (short_id, long_id) VALUES (?, ?)",
        (short_id, long_id)
    )
    conn.commit()
    conn.close()

# ══════════════════════════════════════════════
# Forgetting (遗忘机制)
# ══════════════════════════════════════════════

def forget_expired():
    """清理过期的短期记忆"""
    conn = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 已经整合过且已过期的
    rows = conn.execute("""
        SELECT s.id FROM short_term s
        WHERE s.expires_at IS NOT NULL AND s.expires_at < ?
           OR (s.created_at < ? AND s.importance < 0.5)
    """, (now, (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S"))).fetchall()
    count = len(rows)
    for r in rows:
        conn.execute("DELETE FROM embeddings WHERE scope='short' AND mem_id=?", (r[0],))
        conn.execute("DELETE FROM short_term WHERE id=?", (r[0],))
    conn.commit()
    conn.close()
    return count

def decay_importance():
    """衰减长期记忆中不常访问的记忆权重"""
    conn = get_db()
    threshold = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        UPDATE long_term
        SET confidence = MAX(0.1, confidence * 0.95)
        WHERE last_accessed IS NULL OR last_accessed < ?
    """, (threshold,))
    affected = conn.total_changes
    conn.commit()
    conn.close()
    return affected

# ══════════════════════════════════════════════
# Search (统一检索)
# ══════════════════════════════════════════════

def _embed_mem(scope, mem_id, text):
    """后台 embedding"""
    try:
        vector = get_embedding(text)
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO embeddings (scope, mem_id, vector) VALUES (?, ?, ?)",
            (scope, mem_id, vec_to_blob(vector))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  ⚠️ embedding 失败 [{scope}:{mem_id}]: {e}", file=sys.stderr)

def recall(query, limit=10, mode="hybrid"):
    """
    统一检索：同时搜索短期记忆、长期记忆、memory_sources、memos 和 agent_activity
    mode: fts / vector / hybrid
    """
    results = []

    if mode in ("fts", "hybrid"):
        conn = get_db()
        # 短期记忆 FTS（搜索 segmented 列）
        seg_query = segment(query)
        short_rows = conn.execute("""
            SELECT s.id, s.title, s.content, s.mem_type, s.importance,
                   s.created_at, 'short' as scope, f.rank as score
            FROM short_fts f JOIN short_term s ON s.id = f.rowid
            WHERE short_fts MATCH ?
            ORDER BY f.rank LIMIT ?
        """, (seg_query, limit)).fetchall()
        # 长期记忆 FTS
        long_rows = conn.execute("""
            SELECT l.id, l.title, l.content, l.mem_type, l.importance,
                   l.updated_at as created_at, 'long' as scope, f.rank as score
            FROM long_fts f JOIN long_term l ON l.id = f.rowid
            WHERE long_fts MATCH ?
            ORDER BY f.rank LIMIT ?
        """, (seg_query, limit)).fetchall()
        # Memory Sources FTS
        memory_rows = conn.execute("""
            SELECT m.id, m.block_title as title, m.block_content as content,
                   m.agent_name, m.source_file, m.section_date,
                   m.created_at, 'memory' as scope, f.rank as score
            FROM memory_sources_fts f JOIN memory_sources m ON m.id = f.rowid
            WHERE memory_sources_fts MATCH ?
            ORDER BY f.rank LIMIT ?
        """, (seg_query, limit)).fetchall()
        # Memos FTS
        memo_rows = conn.execute("""
            SELECT m.id, m.title, m.content, m.memo_type as mem_type, m.importance,
                   m.agent_name, m.created_at, 'memo' as scope, f.rank as score
            FROM memos_fts f JOIN memos m ON m.id = f.rowid
            WHERE memos_fts MATCH ?
            ORDER BY f.rank LIMIT ?
        """, (seg_query, limit)).fetchall()
        # Agent Activity FTS
        activity_rows = conn.execute("""
            SELECT a.id, a.title, a.content, a.activity_type as mem_type,
                   a.agent_name, a.created_at, 'activity' as scope, f.rank as score
            FROM activity_fts f JOIN agent_activity a ON a.id = f.rowid
            WHERE activity_fts MATCH ?
            ORDER BY f.rank LIMIT ?
        """, (seg_query, limit)).fetchall()
        conn.close()

        for r in short_rows:
            results.append({"scope": "short", "source": "fts", "score": r["score"],
                          "id": r["id"], "title": r["title"], "content": r["content"][:150],
                          "type": r["mem_type"], "importance": r["importance"],
                          "time": r["created_at"]})
        for r in long_rows:
            results.append({"scope": "long", "source": "fts", "score": r["score"],
                          "id": r["id"], "title": r["title"], "content": r["content"][:150],
                          "type": r["mem_type"], "importance": r["importance"],
                          "time": r["created_at"]})
        for r in memory_rows:
            results.append({"scope": "memory", "source": "fts", "score": r["score"],
                          "id": r["id"], "title": r["title"], "content": r["content"][:150],
                          "type": "memory", "importance": 1.0,
                          "agent": r["agent_name"], "source_file": r["source_file"],
                          "time": r["created_at"]})
        for r in memo_rows:
            results.append({"scope": "memo", "source": "fts", "score": r["score"],
                          "id": r["id"], "title": r["title"], "content": r["content"][:150],
                          "type": r["mem_type"], "importance": r["importance"],
                          "agent": r["agent_name"], "time": r["created_at"]})
        for r in activity_rows:
            results.append({"scope": "activity", "source": "fts", "score": r["score"],
                          "id": r["id"], "title": r["title"], "content": r["content"][:150],
                          "type": r["mem_type"], "importance": 0.5,
                          "agent": r["agent_name"], "time": r["created_at"]})

    if mode in ("vector", "hybrid"):
        try:
            query_vec = get_embedding(query)
        except Exception:
            query_vec = None

        if query_vec:
            conn = get_db()
            emb_rows = conn.execute("SELECT scope, mem_id, vector FROM embeddings").fetchall()
            conn.close()

            scored = []
            for row in emb_rows:
                vec = blob_to_vec(row["vector"])
                sim = cosine_sim(query_vec, vec)
                scored.append((row["scope"], row["mem_id"], sim))

            scored.sort(key=lambda x: x[2], reverse=True)

            for scope, mem_id, sim in scored[:limit]:
                # 检查是否已在 FTS 结果中
                if any(r["scope"] == scope and r["id"] == mem_id for r in results):
                    continue

                conn = get_db()
                if scope == "short":
                    row = conn.execute(
                        "SELECT id, title, content, mem_type, importance, created_at as time FROM short_term WHERE id=?",
                        (mem_id,)
                    ).fetchone()
                elif scope == "long":
                    row = conn.execute(
                        "SELECT id, title, content, mem_type, importance, updated_at as time FROM long_term WHERE id=?",
                        (mem_id,)
                    ).fetchone()
                elif scope == "memory":
                    row = conn.execute(
                        "SELECT id, block_title as title, block_content as content, agent_name, source_file, created_at as time FROM memory_sources WHERE id=?",
                        (mem_id,)
                    ).fetchone()
                else:
                    row = None
                conn.close()

                if row:
                    if scope == "memory":
                        results.append({"scope": scope, "source": "vector", "score": sim,
                                      "id": row["id"], "title": row["title"],
                                      "content": row["content"][:150],
                                      "type": "memory", "importance": 1.0,
                                      "agent": row["agent_name"], "source_file": row["source_file"],
                                      "time": row["time"]})
                    else:
                        results.append({"scope": scope, "source": "vector", "score": sim,
                                      "id": row["id"], "title": row["title"],
                                      "content": row["content"][:150],
                                      "type": row["mem_type"],
                                      "importance": row["importance"],
                                      "time": row["time"]})

    # 排序：长期记忆优先，memory 次之，memo 再次，短期记忆再次，activity 最后
    def sort_key(r):
        scope_boost = {"long": 1.2, "memory": 1.1, "memo": 1.05, "short": 1.0, "activity": 0.9}
        boost = scope_boost.get(r["scope"], 1.0)
        return r["score"] * boost
    results.sort(key=sort_key, reverse=True)

    return results[:limit]

# ══════════════════════════════════════════════
# Status
# ══════════════════════════════════════════════

def status():
    conn = get_db()
    st = conn.execute("SELECT COUNT(*) FROM short_term").fetchone()[0]
    lt = conn.execute("SELECT COUNT(*) FROM long_term").fetchone()[0]
    ent = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    rel = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    emb = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    memos_count = conn.execute("SELECT COUNT(*) FROM memos").fetchone()[0]
    activity_count = conn.execute("SELECT COUNT(*) FROM agent_activity").fetchone()[0]
    memory_count = conn.execute("SELECT COUNT(*) FROM memory_sources").fetchone()[0] if _table_exists(conn, "memory_sources") else 0

    st_types = conn.execute("SELECT mem_type, COUNT(*) c FROM short_term GROUP BY mem_type").fetchall()
    lt_types = conn.execute("SELECT mem_type, COUNT(*) c FROM long_term GROUP BY mem_type").fetchall()
    ent_types = conn.execute("SELECT entity_type, COUNT(*) c FROM entities GROUP BY entity_type").fetchall()

    # Agent 统计
    agent_activity_stats = conn.execute(
        "SELECT agent_name, COUNT(*) c FROM agent_activity GROUP BY agent_name ORDER BY c DESC"
    ).fetchall()
    agent_memo_stats = conn.execute(
        "SELECT agent_name, COUNT(*) c FROM memos GROUP BY agent_name ORDER BY c DESC"
    ).fetchall()
    agent_memory_stats = conn.execute(
        "SELECT agent_name, COUNT(*) c FROM memory_sources GROUP BY agent_name ORDER BY c DESC"
    ).fetchall() if _table_exists(conn, "memory_sources") else []

    recent_short = conn.execute("SELECT title, created_at FROM short_term ORDER BY created_at DESC LIMIT 3").fetchall()
    recent_long = conn.execute("SELECT title, updated_at FROM long_term ORDER BY updated_at DESC LIMIT 3").fetchall()
    recent_memos = conn.execute("SELECT title, agent_name, created_at FROM memos ORDER BY created_at DESC LIMIT 3").fetchall()
    recent_activity = conn.execute(
        "SELECT title, agent_name, activity_type, created_at FROM agent_activity ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    recent_memory = conn.execute(
        "SELECT block_title, agent_name, source_file, created_at FROM memory_sources ORDER BY created_at DESC LIMIT 5"
    ).fetchall() if _table_exists(conn, "memory_sources") else []

    expired = forget_expired()
    decayed = decay_importance()

    conn.close()

    return {
        "short_term": st, "long_term": lt,
        "memory_sources": memory_count,
        "entities": ent, "relations": rel,
        "embeddings": emb,
        "memos": memos_count,
        "activity": activity_count,
        "expired_cleaned": expired,
        "confidence_decayed": decayed,
        "short_types": dict(st_types),
        "long_types": dict(lt_types),
        "entity_types": dict(ent_types),
        "agent_activity_stats": dict(agent_activity_stats),
        "agent_memo_stats": dict(agent_memo_stats),
        "agent_memory_stats": dict(agent_memory_stats),
        "recent_short": recent_short,
        "recent_long": recent_long,
        "recent_memos": recent_memos,
        "recent_activity": recent_activity,
        "recent_memory": recent_memory,
    }

def _table_exists(conn, table_name):
    """检查表是否存在"""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    ).fetchone()
    return row is not None

# ══════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════

def print_usage():
    print("""
Context Hub - 统一记忆系统

记忆操作:
  hub.py init                                    初始化数据库
  hub.py status                                  查看状态

  短期记忆:
  hub.py short-add <type> <title> <content> [options]
      --source src  --tags tag1,tag2  --importance 0.8  --expire 7
  hub.py short-list [--type event|conversation|todo|decision|note]
  hub.py short-get <id>
  hub.py short-del <id>

  长期记忆:
  hub.py long-add <type> <title> <content> [options]
      --source src  --tags tag1,tag2  --importance 0.8  --confidence 0.9
  hub.py long-list [--type fact|person|project|experience|preference|knowledge|relation]
  hub.py long-get <id>
  hub.py long-del <id>
  hub.py long-update <id> [--content ...] [--importance 0.9] [--confidence 0.8]

  Agent 活动:
  hub.py activity-report <agent> <type> <title> <content> [--session-id xxx]
      type: task_completed|decision_made|info_reported|error_occurred|heartbeat_report
  hub.py activity-list [--agent xxx]

  跨 Agent 共享笔记:
  hub.py memo-add <type> <title> <content> [options]
      --agent xxx  --tags tag1,tag2  --importance 0.8  --expire 7
      type: fact|insight|question|answer|cross_reference
  hub.py memo-list [--agent xxx] [--type ...]
  hub.py memo-get <id>
  hub.py memo-del <id>

  实体 & 关系:
  hub.py entity-add <name> <type> [aliases] [description]
  hub.py entity-find <name>
  hub.py entity-list [--type person|project|...]
  hub.py rel-add <from> <to> <type> [description]
  hub.py graph <entity_name>

  整合 & 遗忘:
  hub.py consolidate [--min-importance 0.7] [--min-age 1]
  hub.py forget                                   清理过期记忆 + 衰减权重

  统一检索:
  hub.py recall <query> [--limit 10] [--mode hybrid|fts|vector]
""")

def _parse_opts(args):
    """解析 --options，返回 (opts_dict, consumed_indices_set)"""
    opts = {}
    consumed = set()
    i = 0
    while i < len(args):
        if args[i].startswith("--"):
            key = args[i].lstrip("-")
            if i+1 < len(args) and not args[i+1].startswith("--"):
                opts[key] = args[i+1]
                consumed.add(i)
                consumed.add(i+1)
                i += 2
            else:
                opts[key] = True
                consumed.add(i)
                i += 1
        else:
            i += 1
    return opts, consumed

def _positional_args(args, consumed):
    """从 args 中取出非 option 的位置参数"""
    return [a for i, a in enumerate(args) if i not in consumed and not a.startswith("--")]

def main():
    args = sys.argv[1:]
    if not args or args[0] in ("help", "--help", "-h"):
        print_usage()
        return

    cmd = args[0]

    if cmd == "init":
        init_db()

    elif cmd == "status":
        init_db()
        s = status()
        print("═══ Context Hub 状态 ═══")
        print(f"\n  🧠 短期记忆: {s['short_term']}  |  💎 长期记忆: {s['long_term']}")
        print(f"  📚 Memory Sources: {s['memory_sources']}")
        print(f"  👤 实体: {s['entities']}  |  🔗 关系: {s['relations']}  |  📐 向量: {s['embeddings']}")
        print(f"  📝 Memos: {s['memos']}  |  📊 Agent 活动: {s['activity']}")
        if s["short_types"]:
            print(f"\n  短期记忆类型: {json.dumps(s['short_types'], ensure_ascii=False)}")
        if s["long_types"]:
            print(f"  长期记忆类型: {json.dumps(s['long_types'], ensure_ascii=False)}")
        if s["entity_types"]:
            print(f"  实体类型: {json.dumps(s['entity_types'], ensure_ascii=False)}")
        if s["agent_activity_stats"]:
            print(f"\n  🤖 Agent 活动统计: {json.dumps(s['agent_activity_stats'], ensure_ascii=False)}")
        if s["agent_memo_stats"]:
            print(f"  📝 Agent Memo 统计: {json.dumps(s['agent_memo_stats'], ensure_ascii=False)}")
        if s["agent_memory_stats"]:
            print(f"  📚 Agent Memory 统计: {json.dumps(s['agent_memory_stats'], ensure_ascii=False)}")
        if s["recent_short"]:
            print(f"\n  📥 最近短期记忆:")
            for r in s["recent_short"]:
                print(f"     {r[1]} | {r[0]}")
        if s["recent_long"]:
            print(f"\n  💎 最近长期记忆:")
            for r in s["recent_long"]:
                print(f"     {r[1]} | {r[0]}")
        if s["recent_memory"]:
            print(f"\n  📚 最近 Memory Sources:")
            for r in s["recent_memory"]:
                print(f"     [{r[1]}] {r[3]} | {r[0]} (from {r[2]})")
        if s["recent_memos"]:
            print(f"\n  📝 最近 Memos:")
            for r in s["recent_memos"]:
                print(f"     [{r[1]}] {r[2]} | {r[0]}")
        if s["recent_activity"]:
            print(f"\n  📊 最近活动:")
            for r in s["recent_activity"]:
                print(f"     [{r[1]}] {r[3]} | {r[2]}: {r[0]}")
        print(f"\n  🧹 本次清理过期: {s['expired_cleaned']}  |  📉 权重衰减: {s['confidence_decayed']}")

    elif cmd == "short-add":
        if len(args) < 4:
            print("❌ 用法: hub.py short-add <type> <title> <content>")
            return
        init_db()
        opts, consumed = _parse_opts(args[3:])
        content_parts = _positional_args(args[3:], consumed)
        content = " ".join(content_parts)
        sid = short_add(
            args[1], args[2], content,
            source=opts.get("source", ""),
            tags=opts.get("tags", ""),
            importance=float(opts.get("importance", 0.5)),
            expire_days=int(opts["expire"]) if "expire" in opts else None
        )
        print(f"✅ 短期记忆 #{sid} 已添加")

    elif cmd == "short-list":
        init_db()
        mem_type = None
        if "--type" in args:
            idx = args.index("--type")
            mem_type = args[idx+1] if idx+1 < len(args) else None
        rows = short_list(mem_type)
        if not rows:
            print("📭 空空如也")
            return
        print(f"📋 短期记忆 ({len(rows)} 条)\n")
        for r in rows:
            print(f"  #{r['id']} [{r['mem_type']}] ⭐{r['importance']:.1f} {r['title']}")
            print(f"     {r['content'][:80]}{'...' if len(r['content'])>80 else ''}")
            print(f"     🕐 {r['created_at']}")

    elif cmd == "short-get":
        if len(args) < 2: return
        init_db()
        r = short_get(int(args[1]))
        if r: print(json.dumps(dict(r), ensure_ascii=False, indent=2))
        else: print("❌ 未找到")

    elif cmd == "short-del":
        if len(args) < 2: return
        init_db()
        short_delete(int(args[1]))
        print(f"🗑️ 短期记忆 #{args[1]} 已删除")

    elif cmd == "long-add":
        if len(args) < 4:
            print("❌ 用法: hub.py long-add <type> <title> <content>")
            return
        init_db()
        opts, consumed = _parse_opts(args[3:])
        content_parts = _positional_args(args[3:], consumed)
        lid = long_add(
            args[1], args[2], " ".join(content_parts),
            source=opts.get("source", ""),
            tags=opts.get("tags", ""),
            importance=float(opts.get("importance", 0.5)),
            confidence=float(opts.get("confidence", 1.0))
        )
        print(f"✅ 长期记忆 #{lid} 已添加")

    elif cmd == "long-list":
        init_db()
        mem_type = None
        if "--type" in args:
            idx = args.index("--type")
            mem_type = args[idx+1] if idx+1 < len(args) else None
        rows = long_list(mem_type)
        if not rows:
            print("📭 空空如也")
            return
        print(f"💎 长期记忆 ({len(rows)} 条)\n")
        for r in rows:
            conf = r['confidence'] if 'confidence' in r.keys() else '—'
            print(f"  #{r['id']} [{r['mem_type']}] ⭐{r['importance']:.1f} 🔒{conf} {r['title']}")
            print(f"     {r['content'][:80]}{'...' if len(str(r['content']))>80 else ''}")
            print(f"     🕐 {r.get('updated_at', '—')}")

    elif cmd == "long-get":
        if len(args) < 2: return
        init_db()
        r = long_get(int(args[1]))
        if r: print(json.dumps(dict(r), ensure_ascii=False, indent=2))
        else: print("❌ 未找到")

    elif cmd == "long-del":
        if len(args) < 2: return
        init_db()
        long_delete(int(args[1]))
        print(f"🗑️ 长期记忆 #{args[1]} 已删除")

    elif cmd == "long-update":
        if len(args) < 2: return
        init_db()
        opts, _ = _parse_opts(args[1:])
        long_update(
            int(args[1]),
            content=opts.get("content"),
            importance=float(opts["importance"]) if "importance" in opts else None,
            confidence=float(opts["confidence"]) if "confidence" in opts else None
        )
        print(f"✅ 长期记忆 #{args[1]} 已更新")

    # Agent Activity commands
    elif cmd == "activity-report":
        if len(args) < 5:
            print("❌ 用法: hub.py activity-report <agent> <type> <title> <content>")
            return
        init_db()
        opts, consumed = _parse_opts(args[4:])
        content_parts = _positional_args(args[4:], consumed)
        aid = activity_report(
            args[1], args[2], args[3], " ".join(content_parts),
            session_id=opts.get("session-id", "")
        )
        print(f"✅ 活动记录 #{aid} 已添加")

    elif cmd == "activity-list":
        init_db()
        agent_name = None
        if "--agent" in args:
            idx = args.index("--agent")
            agent_name = args[idx+1] if idx+1 < len(args) else None
        rows = activity_list(agent_name)
        if not rows:
            print("📭 空空如也")
            return
        print(f"📊 Agent 活动 ({len(rows)} 条)\n")
        for r in rows:
            print(f"  #{r['id']} [{r['agent_name']}] [{r['activity_type']}] {r['title']}")
            print(f"     {r['content'][:80]}{'...' if len(r['content'])>80 else ''}")
            print(f"     🕐 {r['created_at']}")

    # Memo commands
    elif cmd == "memo-add":
        if len(args) < 4:
            print("❌ 用法: hub.py memo-add <type> <title> <content>")
            return
        init_db()
        opts, consumed = _parse_opts(args[3:])
        content_parts = _positional_args(args[3:], consumed)
        mid = memo_add(
            opts.get("agent", "unknown"), args[1], args[2], " ".join(content_parts),
            tags=opts.get("tags", ""),
            importance=float(opts.get("importance", 0.5)),
            expire_days=int(opts["expire"]) if "expire" in opts else None,
            related_entities=opts.get("related", "")
        )
        print(f"✅ Memo #{mid} 已添加")

    elif cmd == "memo-list":
        init_db()
        agent_name = None
        memo_type = None
        if "--agent" in args:
            idx = args.index("--agent")
            agent_name = args[idx+1] if idx+1 < len(args) else None
        if "--type" in args:
            idx = args.index("--type")
            memo_type = args[idx+1] if idx+1 < len(args) else None
        rows = memo_list(agent_name, memo_type)
        if not rows:
            print("📭 空空如也")
            return
        print(f"📝 Memos ({len(rows)} 条)\n")
        for r in rows:
            print(f"  #{r['id']} [{r['agent_name']}] [{r['memo_type']}] ⭐{r['importance']:.1f} {r['title']}")
            print(f"     {r['content'][:80]}{'...' if len(r['content'])>80 else ''}")
            print(f"     🏷️ {r['tags']}  🕐 {r['created_at']}")

    elif cmd == "memo-get":
        if len(args) < 2: return
        init_db()
        r = memo_get(int(args[1]))
        if r: print(json.dumps(dict(r), ensure_ascii=False, indent=2))
        else: print("❌ 未找到")

    elif cmd == "memo-del":
        if len(args) < 2: return
        init_db()
        memo_delete(int(args[1]))
        print(f"🗑️ Memo #{args[1]} 已删除")

    elif cmd == "entity-add":
        if len(args) < 3: return
        init_db()
        aliases = args[3] if len(args) > 3 else ""
        desc = args[4] if len(args) > 4 else ""
        eid = entity_add(args[1], args[2], aliases, desc)
        print(f"✅ 实体 #{eid}: {args[1]} ({args[2]})")

    elif cmd == "entity-find":
        if len(args) < 2: return
        init_db()
        r = entity_find(args[1])
        if r: print(json.dumps(dict(r), ensure_ascii=False, indent=2))
        else: print("❌ 未找到实体")

    elif cmd == "entity-list":
        init_db()
        etype = None
        if "--type" in args:
            idx = args.index("--type")
            etype = args[idx+1] if idx+1 < len(args) else None
        rows = entity_list(etype)
        for r in rows:
            print(f"  #{r['id']} [{r['entity_type']}] {r['name']} — {r['description'][:50]}")

    elif cmd == "rel-add":
        if len(args) < 4: return
        init_db()
        desc = args[4] if len(args) > 4 else ""
        relation_add(args[1], args[2], args[3], desc)
        print(f"✅ 关系: {args[1]} → [{args[3]}] → {args[2]}")

    elif cmd == "graph":
        if len(args) < 2: return
        init_db()
        g = entity_graph(args[1])
        if not g:
            print(f"❌ 未找到实体: {args[1]}")
            return
        print(f"📊 {g['entity']['name']} ({g['entity']['entity_type']})")
        if g['entity']['description']:
            print(f"   {g['entity']['description']}")
        for r in g["relations"]:
            direction = "→" if r["from_name"] == g["entity"]["name"] else "←"
            other = r["to_name"] if direction == "→" else r["from_name"]
            print(f"   {direction} [{r['rel_type']}] {other} ({r['to_type']})")

    elif cmd == "consolidate":
        init_db()
        opts, _ = _parse_opts(args[1:])
        min_imp = float(opts.get("min-importance", 0.7))
        min_age = int(opts.get("min-age", 1))
        candidates = consolidate_candidates(min_imp, min_age)
        if not candidates:
            print("📭 没有需要整合的短期记忆")
            return
        print(f"📋 整合候选 ({len(candidates)} 条):\n")
        for r in candidates:
            print(f"  #{r['id']} [{r['mem_type']}] ⭐{r['importance']:.1f} {r['title']}")
            print(f"     {r['content'][:100]}")

    elif cmd == "forget":
        init_db()
        expired = forget_expired()
        decayed = decay_importance()
        print(f"🧹 清理过期短期记忆: {expired} 条")
        print(f"📉 长期记忆权重衰减: {decayed} 条")

    elif cmd == "recall":
        if len(args) < 2:
            print("❌ 用法: hub.py recall <query>")
            return
        init_db()
        opts, consumed = _parse_opts(args[1:])
        query_parts = _positional_args(args[1:], consumed)
        query = " ".join(query_parts)
        limit = int(opts.get("limit", 10))
        mode = opts.get("mode", "hybrid")
        results = recall(query, limit, mode)
        if not results:
            print("🔍 无结果")
            return
        print(f"🔍 检索结果 ({len(results)} 条):\n")
        scope_icon = {"short": "🧠", "long": "💎", "memo": "📝", "activity": "📊"}
        source_label = {"fts": "关键词", "vector": "语义"}
        for r in results:
            icon = scope_icon.get(r["scope"], "?")
            label = source_label.get(r["source"], "?")
            agent_info = f" [{r.get('agent', '')}]" if r.get("agent") else ""
            print(f"  {icon} #{r['id']}{agent_info} [{r['type']}] {r['title']}")
            print(f"     📊 {label} {r['score']:.4f}  ⭐{r['importance']:.1f}")
            print(f"     💬 {r['content']}")
            print(f"     🕐 {r['time']}")

    else:
        print(f"❌ 未知命令: {cmd}")
        print_usage()

if __name__ == "__main__":
    main()
