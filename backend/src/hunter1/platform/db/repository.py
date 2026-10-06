"""仓储实现 —— SQLite 版，实现 application 层定义的端口。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from hunter1.domain.models import CaptureStatus, Company, Job
from hunter1.platform.db.schema import CompanyRow, JobRow
from hunter1.platform.text import normalize_job_title

if TYPE_CHECKING:
    from hunter1.platform.db.database import Database


def _to_company(row: CompanyRow) -> Company:
    return Company(
        id=row.id,
        name=row.name,
        source=row.source,
        source_ref=row.source_ref,
        aliases=list(row.aliases or []),
        campus_url=row.campus_url,
        crawler_key=row.crawler_key,
    )


def _to_job(row: JobRow) -> Job:
    return Job(
        id=row.id,
        company_id=row.company_id,
        title=row.title,
        detail_url=row.detail_url,
        source=row.source,
        source_ref=row.source_ref,
        company_name=row.company_name,
        city=row.city,
        jd_raw=row.jd_raw,
        match_score=row.match_score,
        capture_status=CaptureStatus(row.capture_status),
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
    )


class SqliteCompanyRepository:
    """公司仓储：upsert 语义（存在即更新，否则插入）。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    def upsert(self, company: Company) -> None:
        with self._db.session() as session:
            row = session.get(CompanyRow, company.id)
            if row is None:
                row = CompanyRow(id=company.id)
                session.add(row)
            row.name = company.name
            row.source = company.source
            row.source_ref = company.source_ref
            row.aliases = list(company.aliases)
            row.campus_url = company.campus_url
            row.crawler_key = company.crawler_key
            session.commit()

    def get(self, company_id: str) -> Company | None:
        with self._db.session() as session:
            row = session.get(CompanyRow, company_id)
            return _to_company(row) if row is not None else None

    def count(self) -> int:
        with self._db.session() as session:
            total = session.scalar(select(func.count()).select_from(CompanyRow))
            return int(total or 0)


def _escape_like(text: str) -> str:
    """转义 LIKE 通配符。

    SQLite 的 LIKE 默认把 `%`（任意串）与 `_`（任意单字符）当通配符；标题归一化
    不剥离标点，所以标题里含这些字符、或用户拿它们搜索时，会匹配到意料之外的行。
    显式转义后按**字面**匹配（配合 `escape="\\\\"`）。
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _assign_facts(row: JobRow, job: Job) -> None:
    """把岗位的**事实列**写进行对象（不含 match_score）。

    match_score 由评分切片单独拥有（见 `set_match_score`），抓取路径不得在此写它。
    """
    row.company_id = job.company_id
    row.title = job.title
    row.title_key = job.title_key
    row.detail_url = job.detail_url
    row.source = job.source
    row.source_ref = job.source_ref
    row.company_name = job.company_name
    row.city = job.city
    row.jd_raw = job.jd_raw
    row.capture_status = job.capture_status.value
    row.first_seen_at = job.first_seen_at
    row.last_seen_at = job.last_seen_at


class SqliteJobRepository:
    """岗位仓储。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    def upsert(self, job: Job) -> None:
        """全字段 upsert（**含** match_score）。

        仅供创建、种子与测试使用。抓取路径请用 `upsert_facts`：整行写入会把评分
        并发写下的 match_score 覆盖回旧值（读-改-写丢更新竞态）。
        """
        with self._db.session() as session:
            row = session.get(JobRow, job.id)
            if row is None:
                row = JobRow(id=job.id)
                session.add(row)
            _assign_facts(row, job)
            row.match_score = job.match_score
            session.commit()

    def upsert_facts(self, job: Job) -> None:
        """抓取路径的写入：只写事实列，**不触碰 match_score**。

        这样 crawl 与 scoring 各写各的列，二者并发的读-改-写不再互相覆盖。
        岗位不存在时照常插入（match_score 保持默认 NULL，由评分填充）。
        """
        with self._db.session() as session:
            row = session.get(JobRow, job.id)
            if row is None:
                row = JobRow(id=job.id)
                session.add(row)
            _assign_facts(row, job)
            session.commit()

    def set_match_score(self, job_id: str, score: int) -> bool:
        """只更新 match_score 一列；返回是否有行被更新（False = 岗位不存在）。

        ORM 只把**变更过的属性**写进 UPDATE，所以这里读一行再改属性，落到 SQL 仍是
        单列 `UPDATE ... SET match_score=? WHERE id=?` —— 不会像整行 upsert 那样把
        抓取线程同时更新的标题/城市/JD 回滚掉。
        """
        with self._db.session() as session:
            row = session.get(JobRow, job_id)
            if row is None:
                return False
            row.match_score = score
            session.commit()
            return True

    def get(self, job_id: str) -> Job | None:
        with self._db.session() as session:
            row = session.get(JobRow, job_id)
            return _to_job(row) if row is not None else None

    def get_by_prefix(self, prefix: str) -> list[Job]:
        """按 id 前缀查找（助手常只看到前 8 位 id）。

        下推到 SQL 的前缀匹配 —— 不在内存里扫「最近 N 条」碰运气：
        那种做法既随库增长变慢，也会漏掉窗口之外的真实匹配。
        空前缀返回空：全表不是「一个前缀」。
        """
        if not prefix:
            return []
        escaped = _escape_like(prefix)
        with self._db.session() as session:
            statement = (
                select(JobRow)
                .where(JobRow.id.like(f"{escaped}%", escape="\\"))
                .order_by(JobRow.last_seen_at.desc())
            )
            return [_to_job(row) for row in session.scalars(statement)]

    def count(self, *, company_id: str | None = None, keyword: str | None = None) -> int:
        """计数。`keyword` 与 `search` 用同一套归一化匹配，保证「共 N 条」不虚报。"""
        with self._db.session() as session:
            statement = select(func.count()).select_from(JobRow)
            if company_id is not None:
                statement = statement.where(JobRow.company_id == company_id)
            if keyword is not None and (normalized := normalize_job_title(keyword)):
                statement = statement.where(
                    JobRow.title_key.like(f"%{_escape_like(normalized)}%", escape="\\")
                )
            total = session.scalar(statement)
            return int(total or 0)

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Job]:
        with self._db.session() as session:
            statement = (
                select(JobRow).order_by(JobRow.last_seen_at.desc()).limit(limit).offset(offset)
            )
            return [_to_job(row) for row in session.scalars(statement)]

    def search(self, *, keyword: str, limit: int = 20, offset: int = 0) -> list[Job]:
        """按关键词搜索岗位（在归一化标题上匹配，大小写/全角/括号不敏感）。

        空关键词退化为「列出最近岗位」—— 助手问「有什么岗位」时的用法。
        """
        normalized = normalize_job_title(keyword)
        with self._db.session() as session:
            statement = select(JobRow).order_by(JobRow.last_seen_at.desc())
            if normalized:
                statement = statement.where(
                    JobRow.title_key.like(f"%{_escape_like(normalized)}%", escape="\\")
                )
            statement = statement.limit(limit).offset(offset)
            return [_to_job(row) for row in session.scalars(statement)]


__all__ = ["SqliteCompanyRepository", "SqliteJobRepository"]
