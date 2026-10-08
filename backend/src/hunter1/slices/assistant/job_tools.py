"""求职助手可调用的真实工具 —— 读岗位库。

工具都是**只读**的：助手负责查询与建议，写操作（记录投递等）由用户在界面上确认。
这与「助手不可用时优雅降级」的设计一致 —— 助手不是唯一入口。

新增一个工具 = 写一个带类型标注的函数 + 用 `@tool` 注册，不改其他代码。
"""

from __future__ import annotations

from hunter1.application.ports import ApplicationRepository, JobRepository
from hunter1.slices.assistant.tools import Tool, ToolRegistry, tool

SEARCH_LIMIT_MAX = 50

# 阶段的中文名（给助手读的消息用）
_STAGE_LABELS: dict[str, str] = {
    "applied": "已投递",
    "written_test": "笔试",
    "interview": "面试",
    "offer": "Offer",
    "rejected": "已拒",
    "withdrawn": "已放弃",
}


def build_tools(
    *,
    jobs: JobRepository,
    applications: ApplicationRepository | None = None,
) -> ToolRegistry:
    """按给定的仓储装配工具集。

    `applications` 不传时不注册投递查询工具 —— 让「没接线」表现为「工具不存在」，
    而不是「工具存在但总是报错」。
    """

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
            # 支持用前缀查找（模型常只看到前 8 位）——查询下推到 SQL，
            # 不在内存里扫「最近 N 条」（那会随库增长变慢，且漏掉窗口外的匹配）
            candidates = jobs.get_by_prefix(job_id)
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

    if applications is not None:

        def application_query(job_id: str = "") -> str:
            """查询投递记录与所处阶段。

            不传 job_id 时列出全部投递；传了则只看该岗位的投递情况。
            **岗位 id 可以只给前几位**（`search_jobs` 展示的就是 8 位前缀）——
            只做精确匹配会让模型按展示格式传参时得到「没有投递记录」这种假否定。
            """
            if job_id:
                found = applications.by_job(job_id)
                if not found:
                    # 与 job_detail 同一套：精确不中再按前缀找（模型常只记得前几位 id）
                    candidates = applications.by_job_prefix(job_id, limit=SEARCH_LIMIT_MAX)
                    if len(candidates) > 1:
                        return (
                            f"岗位 id 前缀 {job_id} 有 {len(candidates)} 条投递匹配，"
                            "请给更长的 id。"
                        )
                    found = candidates
                if not found:
                    return f"没有查到岗位 {job_id} 的投递记录。"
            else:
                found = applications.list(limit=SEARCH_LIMIT_MAX)
                if not found:
                    return "还没有投递记录。"

            lines = [f"共 {len(found)} 条投递："]
            for item in found:
                stage = _STAGE_LABELS.get(item.stage.value, item.stage.value)
                when = item.updated_at.strftime("%Y-%m-%d")
                note = f"，备注：{item.note}" if item.note else ""
                lines.append(f"- {item.company} · {item.title} — {stage}（{when}）{note}")
            return "\n".join(lines)

        tools.append(
            tool(
                application_query,
                name="application_query",
                description=(application_query.__doc__ or "").strip(),
            )
        )

    return ToolRegistry(tools)


__all__ = ["SEARCH_LIMIT_MAX", "build_tools"]
