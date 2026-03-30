#!/usr/bin/env python3
"""Context Hub MCP Server — 将统一记忆系统封装为 MCP 工具

供 OpenClaw 的 agent 通过 MCP 协议调用记忆系统。
"""

from typing import Optional
from mcp.server.fastmcp import FastMCP

from hub import (
    init_db,
    recall,
    short_add,
    long_add,
    memo_add,
    activity_report,
    entity_add,
    relation_add,
    entity_graph,
    status,
    forget_expired,
    decay_importance,
    consolidate_candidates,
)

# 确保 DB 已初始化
init_db()

mcp = FastMCP("context-hub")


@mcp.tool()
def ctx_recall(query: str, limit: int = 10, mode: str = "hybrid") -> str:
    """统一检索：同时搜索短期记忆、长期记忆、共享笔记和活动流。

    Args:
        query: 搜索关键词或自然语言描述
        limit: 返回结果数量上限，默认 10
        mode: 检索模式，"hybrid"（混合，默认）、"fts"（关键词）、"vector"（语义向量）

    Returns:
        匹配结果列表，包含来源、类型、标题、内容摘要和分数
    """
    results = recall(query, limit, mode)
    if not results:
        return "未找到匹配结果。"

    scope_icon = {"short": "短期记忆", "long": "长期记忆", "memo": "共享笔记", "activity": "活动流"}
    source_label = {"fts": "关键词匹配", "vector": "语义相似"}

    lines = [f"检索到 {len(results)} 条结果:\n"]
    for i, r in enumerate(results, 1):
        scope = scope_icon.get(r["scope"], r["scope"])
        src = source_label.get(r["source"], r["source"])
        agent_info = f" [{r['agent']}]" if r.get("agent") else ""
        lines.append(f"{i}. [{scope}] #{r['id']}{agent_info} ({r['type']}) {r['title']}")
        lines.append(f"   来源: {src} | 分数: {r['score']:.4f} | 重要性: {r['importance']:.1f}")
        lines.append(f"   内容: {r['content']}")
        lines.append(f"   时间: {r['time']}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def ctx_short_add(
    mem_type: str,
    title: str,
    content: str,
    agent_name: Optional[str] = None,
    source: Optional[str] = None,
    tags: Optional[str] = None,
    importance: float = 0.5,
    expire_days: Optional[int] = None,
) -> str:
    """添加短期记忆：用于存储最近的事件、对话摘要、待办等临时信息。

    Args:
        mem_type: 记忆类型，可选值: event（事件）, conversation（对话）, todo（待办）, decision（决策）, note（笔记）
        title: 标题
        content: 内容
        agent_name: 产生此记忆的 agent 名称（可选，当前版本暂未使用）
        source: 来源标识（可选）
        tags: 标签，逗号分隔（可选）
        importance: 重要性 0.0-1.0，默认 0.5
        expire_days: 过期天数，过期后会被自动清理（可选）

    Returns:
        添加结果
    """
    sid = short_add(
        mem_type=mem_type,
        title=title,
        content=content,
        source=source or "",
        tags=tags or "",
        importance=importance,
        expire_days=expire_days,
    )
    expire_info = f"，{expire_days} 天后过期" if expire_days else ""
    return f"已添加短期记忆 #{sid} [{mem_type}] \"{title}\"，重要性 {importance}{expire_info}"


@mcp.tool()
def ctx_long_add(
    mem_type: str,
    title: str,
    content: str,
    agent_name: Optional[str] = None,
    source: Optional[str] = None,
    tags: Optional[str] = None,
    importance: float = 0.5,
    confidence: float = 1.0,
) -> str:
    """添加长期记忆：用于持久化知识、经验、人物、项目等稳定信息。

    Args:
        mem_type: 记忆类型，可选值: fact（事实）, person（人物）, project（项目）, experience（经验）, preference（偏好）, knowledge（知识）, relation（关系）
        title: 标题
        content: 内容
        agent_name: 产生此记忆的 agent 名称（可选，当前版本暂未使用）
        source: 来源标识（可选）
        tags: 标签，逗号分隔（可选）
        importance: 重要性 0.0-1.0，默认 0.5
        confidence: 置信度 0.0-1.0，默认 1.0

    Returns:
        添加结果
    """
    lid = long_add(
        mem_type=mem_type,
        title=title,
        content=content,
        source=source or "",
        tags=tags or "",
        importance=importance,
        confidence=confidence,
    )
    return f"已添加长期记忆 #{lid} [{mem_type}] \"{title}\"，重要性 {importance}，置信度 {confidence}"


@mcp.tool()
def ctx_memo_add(
    agent_name: str,
    memo_type: str,
    title: str,
    content: str,
    tags: Optional[str] = None,
    importance: float = 0.5,
    expire_days: Optional[int] = None,
) -> str:
    """添加跨 agent 共享笔记：供不同 agent 之间交换信息和洞察。

    Args:
        agent_name: 发布笔记的 agent 名称
        memo_type: 笔记类型，可选值: fact（事实）, insight（洞察）, question（问题）, answer（回答）, cross_reference（交叉引用）
        title: 标题
        content: 内容
        tags: 标签，逗号分隔（可选）
        importance: 重要性 0.0-1.0，默认 0.5
        expire_days: 过期天数（可选）

    Returns:
        添加结果
    """
    mid = memo_add(
        agent_name=agent_name,
        memo_type=memo_type,
        title=title,
        content=content,
        tags=tags or "",
        importance=importance,
        expire_days=expire_days,
    )
    return f"已添加共享笔记 #{mid} [{memo_type}] \"{title}\"，来自 {agent_name}"


@mcp.tool()
def ctx_activity_report(
    agent_name: str,
    activity_type: str,
    title: str,
    content: str,
    session_id: Optional[str] = None,
) -> str:
    """上报 agent 活动：记录 agent 完成的任务、做出的决策等信息到活动流。

    Args:
        agent_name: agent 名称
        activity_type: 活动类型，可选值: task_completed（任务完成）, decision_made（决策）, info_reported（信息上报）, error_occurred（错误发生）, heartbeat_report（心跳）
        title: 活动标题
        content: 活动内容
        session_id: 会话 ID（可选）

    Returns:
        上报结果
    """
    aid = activity_report(
        agent_name=agent_name,
        activity_type=activity_type,
        title=title,
        content=content,
        session_id=session_id or "",
    )
    return f"已记录活动 #{aid} [{activity_type}] \"{title}\"，来自 {agent_name}"


@mcp.tool()
def ctx_entity_add(
    name: str,
    entity_type: str,
    aliases: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """添加实体：注册人、项目、组织、工具、概念等到知识图谱。

    Args:
        name: 实体名称（唯一标识）
        entity_type: 实体类型，可选值: person（人物）, project（项目）, org（组织）, tool（工具）, concept（概念）, location（地点）
        aliases: 别名，逗号分隔（可选）
        description: 实体描述（可选）

    Returns:
        添加结果
    """
    eid = entity_add(
        name=name,
        entity_type=entity_type,
        aliases=aliases or "",
        description=description or "",
    )
    if eid:
        return f"已添加实体 #{eid} \"{name}\" ({entity_type})"
    else:
        return f"实体 \"{name}\" 已存在"


@mcp.tool()
def ctx_rel_add(
    from_name: str,
    to_name: str,
    rel_type: str,
    description: Optional[str] = None,
) -> str:
    """添加关系：在两个实体之间建立连接。自动查找或创建实体。

    Args:
        from_name: 起始实体名称
        to_name: 目标实体名称
        rel_type: 关系类型，如 member_of, works_with, depends_on 等
        description: 关系描述（可选）

    Returns:
        添加结果
    """
    relation_add(
        from_name=from_name,
        to_name=to_name,
        rel_type=rel_type,
        description=description or "",
    )
    return f"已添加关系: {from_name} → [{rel_type}] → {to_name}"


@mcp.tool()
def ctx_graph(entity_name: str) -> str:
    """查询实体关系图：获取指定实体及其所有直接关系。

    Args:
        entity_name: 实体名称或别名

    Returns:
        实体信息及其关系列表
    """
    g = entity_graph(entity_name)
    if not g:
        return f"未找到实体: {entity_name}"

    ent = g["entity"]
    lines = [f"实体: {ent['name']} ({ent['entity_type']})"]
    if ent.get("description"):
        lines.append(f"描述: {ent['description']}")

    if g["relations"]:
        lines.append(f"\n关系 ({len(g['relations'])} 条):")
        for r in g["relations"]:
            direction = "→" if r["from_name"] == ent["name"] else "←"
            other = r["to_name"] if direction == "→" else r["from_name"]
            desc = f" — {r['description']}" if r.get("description") else ""
            lines.append(f"  {direction} [{r['rel_type']}] {other}{desc}")
    else:
        lines.append("\n暂无关系记录。")

    return "\n".join(lines)


@mcp.tool()
def ctx_status() -> str:
    """查看 Context Hub 当前状态：各类记忆数量、实体统计、最近活动等概览。

    Returns:
        系统状态概览
    """
    s = status()

    lines = [
        "═══ Context Hub 状态 ═══",
        "",
        f"短期记忆: {s['short_term']}  |  长期记忆: {s['long_term']}",
        f"实体: {s['entities']}  |  关系: {s['relations']}  |  向量: {s['embeddings']}",
        f"共享笔记: {s['memos']}  |  活动记录: {s['activity']}",
    ]

    if s.get("short_types"):
        lines.append(f"\n短期记忆类型分布: {s['short_types']}")
    if s.get("long_types"):
        lines.append(f"长期记忆类型分布: {s['long_types']}")
    if s.get("entity_types"):
        lines.append(f"实体类型分布: {s['entity_types']}")
    if s.get("agent_activity_stats"):
        lines.append(f"\nAgent 活动统计: {s['agent_activity_stats']}")
    if s.get("agent_memo_stats"):
        lines.append(f"Agent 笔记统计: {s['agent_memo_stats']}")

    lines.append(f"\n本次清理过期: {s['expired_cleaned']} 条  |  权重衰减: {s['confidence_decayed']} 条")

    return "\n".join(lines)


@mcp.tool()
def ctx_forget() -> str:
    """执行遗忘清理：清理过期的短期记忆，并衰减长期记忆中不常访问的记忆权重。

    Returns:
        清理结果
    """
    expired = forget_expired()
    decayed = decay_importance()
    return f"遗忘清理完成: 清理过期短期记忆 {expired} 条，长期记忆权重衰减 {decayed} 条"


@mcp.tool()
def ctx_consolidate(
    min_importance: float = 0.7,
    min_age_days: int = 1,
) -> str:
    """查看整合候选：列出适合从短期记忆整合到长期记忆的候选条目。

    Args:
        min_importance: 最低重要性阈值，默认 0.7
        min_age_days: 最小年龄天数，默认 1 天

    Returns:
        候选整合的短期记忆列表
    """
    candidates = consolidate_candidates(min_importance, min_age_days)
    if not candidates:
        return "没有需要整合的短期记忆。"

    lines = [f"整合候选 ({len(candidates)} 条):\n"]
    for r in candidates:
        lines.append(f"  #{r['id']} [{r['mem_type']}] 重要性 {r['importance']:.1f} \"{r['title']}\"")
        lines.append(f"     {r['content'][:100]}")
        lines.append(f"     创建于 {r['created_at']}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
