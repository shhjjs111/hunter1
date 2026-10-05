"""求职助手可调用的真实工具 —— 读岗位库。

工具都是**只读**的：助手负责查询与建议，写操作（记录投递等）由用户在界面上确认。
这与「助手不可用时优雅降级」的设计一致 —— 助手不是唯一入口。

新增一个工具 = 写一个带类型标注的函数 + 用 `@tool` 注册，不改其他代码。
"""

from __future__ import annotations

from hunter1.application.ports import JobRepository
from hunter1.application.tools import Tool, ToolRegistry, tool

SEARCH_LIMIT_MAX = 50


def build_tools(*, jobs: JobRepository) -> ToolRegistry:
    """按给定的仓储装配工具集。"""

    def search_jobs(keyword: str = "", limit: int = 10) -> str:
        """按关键词搜索岗位库。

        keyword 为空时列出最近的岗位。匹配对大小写、全角/半角、括号补充说明不敏感。
        """
        capped = max(1, min(int(limit), SEARCH_LIMIT_MAX))
        found = jobs.search(keyword=keyword, limit=capped)
        if not found:
            scope = f"「{keyword}」" if keyword.strip() else "（全部）"
            return f"没有找到匹配 {scope} 的岗位。"
        total = jobs.count()
        lines = [f"匹配到 {len(found)} 条（岗位库共 {total} 条）："]
        for job in found:
            score = f"，匹配分 {job.match_score}" if job.match_score is not None else "，未评分"
            city = f"，{job.city}" if job.city else ""
            lines.append(f"- [{job.id[:8]}] {job.title}{city}{score}")
        return "\n".join(lines)

    def job_detail(job_id: str) -> str:
        """查看某个岗位的完整信息（按 id 前缀或全 id）。"""
        job = jobs.get(job_id)
        if job is None:
            # 支持用前缀查找（模型常只看到前 8 位）
            candidates = [j for j in jobs.list(limit=500) if j.id.startswith(job_id)]
            if len(candidates) == 1:
                job = candidates[0]
            elif len(candidates) > 1:
                return f"id 前缀 {job_id} 有 {len(candidates)} 条匹配，请给更长的 id。"
            else:
                return f"找不到岗位 {job_id}。"
        parts = [
            f"岗位：{job.title}",
            f"id：{job.id}",
            f"城市：{job.city or '未知'}",
            f"详情链接：{job.detail_url}",
            f"抓取状态：{job.capture_status.value}",
            f"匹配分：{job.match_score if job.match_score is not None else '未评分'}",
        ]
        if job.jd_raw:
            parts.append(f"岗位描述：\n{job.jd_raw[:1500]}")
        else:
            parts.append("岗位描述：未抓到")
        return "\n".join(parts)

    def job_stats() -> str:
        """给出岗位库的统计概览。"""
        total = jobs.count()
        recent = jobs.list(limit=5)
        lines = [f"岗位库共 {total} 条。"]
        if recent:
            lines.append("最近入库：")
            lines.extend(f"- {job.title}" for job in recent)
        return "\n".join(lines)

    tools: list[Tool] = [
        tool(search_jobs, name="search_jobs", description=(search_jobs.__doc__ or "").strip()),
        tool(job_detail, name="job_detail", description=(job_detail.__doc__ or "").strip()),
        tool(job_stats, name="job_stats", description=(job_stats.__doc__ or "").strip()),
    ]
    return ToolRegistry(tools)


__all__ = ["SEARCH_LIMIT_MAX", "build_tools"]
