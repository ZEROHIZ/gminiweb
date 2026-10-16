"""
backend/log_service.py

核心职责：
请求日志持久化与审计管理。基于 aiosqlite 与 WAL 模式提供轻量级高并发审计日志记录、
多条件分页检索、单条详情拉取与根据上限/保留期自动滚动淘汰。

主要功能：
1. LogService: 单例管理器，负责数据库初始化（启用 WAL 模式）、表结构生命周期管理。
2. add_log: 异步记录调用日志（涵盖操作类型、Token、状态、耗时、请求报文、响应报文及图片 URL）。
3. get_logs: 提供带状态与操作类型过滤的分页查询。
4. get_log_by_id: 获取单条日志完整字段供详情弹窗展示。
5. clear_logs: 一键清空所有历史日志。
6. cleanup_old_logs: 按照最大保留条数和最长保存天数自动清理早期日志。
"""

import datetime
import json
import os
from pathlib import Path
import aiosqlite


class LogService:
    def __init__(self, data_dir: str | Path | None = None):
        base_dir = Path(data_dir or os.getenv("DATA_DIR", "data"))
        self.data_dir = base_dir
        self.db_path = base_dir / "logs.db"
        self._initialized = False

    async def init_db(self) -> None:
        """初始化 SQLite 数据库并开启 WAL 模式与防死锁超时"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT NOT NULL,
                    token_email TEXT,
                    status TEXT NOT NULL,
                    progress TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    summary TEXT,
                    duration REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    request_data TEXT,
                    response_data TEXT,
                    error_reason TEXT,
                    error_response TEXT,
                    result_url TEXT
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_logs_created ON logs (id DESC);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_logs_op ON logs (operation);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_logs_status ON logs (status);")
            await db.commit()
        self._initialized = True

    async def _ensure_db(self) -> None:
        if not self._initialized:
            await self.init_db()

    async def add_log(
        self,
        operation: str,
        token_email: str | None,
        status: str,
        progress: str,
        status_code: int,
        summary: str,
        duration: float,
        request_data: dict | str | None = None,
        response_data: dict | str | None = None,
        error_reason: str | None = None,
        error_response: dict | str | None = None,
        result_url: str | None = None,
        created_at: str | None = None,
    ) -> int:
        """记录一条结构化请求日志"""
        await self._ensure_db()
        if created_at is None:
            created_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        req_str = json.dumps(request_data, ensure_ascii=False) if isinstance(request_data, (dict, list)) else (request_data or "")
        resp_str = json.dumps(response_data, ensure_ascii=False) if isinstance(response_data, (dict, list)) else (response_data or "")
        err_resp_str = json.dumps(error_response, ensure_ascii=False) if isinstance(error_response, (dict, list)) else (error_response or "")

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO logs (
                    operation, token_email, status, progress, status_code,
                    summary, duration, created_at, request_data, response_data,
                    error_reason, error_response, result_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation,
                    token_email or "未知",
                    status,
                    progress,
                    status_code,
                    summary,
                    round(duration, 2),
                    created_at,
                    req_str,
                    resp_str,
                    error_reason or "",
                    err_resp_str,
                    result_url or "",
                ),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def get_logs(
        self,
        page: int = 1,
        page_size: int = 10,
        status: str | None = None,
        operation: str | None = None,
    ) -> dict:
        """带筛选条件的分页查询日志列表"""
        await self._ensure_db()
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        offset = (page - 1) * page_size

        where_clauses = []
        params = []
        if status and status.strip():
            where_clauses.append("status = ?")
            params.append(status.strip())
        if operation and operation.strip():
            where_clauses.append("operation = ?")
            params.append(operation.strip())

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            count_query = f"SELECT COUNT(*) FROM logs {where_sql}"
            async with db.execute(count_query, params) as cursor:
                total_row = await cursor.fetchone()
                total = total_row[0] if total_row else 0

            list_query = f"""
                SELECT id, operation, token_email, status, progress, status_code,
                       summary, duration, created_at, result_url
                FROM logs
                {where_sql}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
            """
            async with db.execute(list_query, params + [page_size, offset]) as cursor:
                rows = await cursor.fetchall()
                items = [dict(row) for row in rows]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items,
        }

    async def get_log_by_id(self, log_id: int) -> dict | None:
        """获取指定日志的完整详情（用于前端详情弹窗）"""
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM logs WHERE id = ?", (log_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                for json_col in ("request_data", "response_data", "error_response"):
                    if data.get(json_col):
                        try:
                            data[json_col] = json.loads(data[json_col])
                        except Exception:
                            pass
                return data

    async def get_account_health_logs(
        self,
        token_identifiers: list[str] | str,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """专门查询特定账号的有效性巡检与保活轮换日志"""
        await self._ensure_db()
        if isinstance(token_identifiers, str):
            token_identifiers = [token_identifiers]
        token_identifiers = [t for t in token_identifiers if t]
        if not token_identifiers:
            return {"items": [], "total": 0, "page": page, "page_size": page_size, "stats": {}}

        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        offset = (page - 1) * page_size

        placeholders = ",".join(["?"] * len(token_identifiers))

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            stats_query = f"""
                SELECT 
                    COUNT(*) as total_records,
                    SUM(CASE WHEN operation = 'health_check' THEN 1 ELSE 0 END) as total_checks,
                    SUM(CASE WHEN operation = 'keep_alive' THEN 1 ELSE 0 END) as total_keep_alives,
                    MAX(CASE WHEN operation = 'health_check' THEN created_at ELSE NULL END) as last_checked_at,
                    MAX(CASE WHEN operation = 'keep_alive' THEN created_at ELSE NULL END) as last_keep_alive_at
                FROM logs
                WHERE token_email IN ({placeholders}) AND operation IN ('health_check', 'keep_alive')
            """
            async with db.execute(stats_query, token_identifiers) as cursor:
                stats_row = await cursor.fetchone()
                stats = dict(stats_row) if stats_row else {}

            total = stats.get("total_records") or 0

            list_query = f"""
                SELECT id, operation, token_email, status, progress, status_code,
                       summary, duration, created_at, request_data, response_data, error_reason
                FROM logs
                WHERE token_email IN ({placeholders}) AND operation IN ('health_check', 'keep_alive')
                ORDER BY id DESC
                LIMIT ? OFFSET ?
            """
            async with db.execute(list_query, token_identifiers + [page_size, offset]) as cursor:
                rows = await cursor.fetchall()
                items = [dict(row) for row in rows]

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total > 0 else 1,
            "stats": {
                "total_checks": stats.get("total_checks") or 0,
                "total_keep_alives": stats.get("total_keep_alives") or 0,
                "last_checked_at": stats.get("last_checked_at") or "-",
                "last_keep_alive_at": stats.get("last_keep_alive_at") or "-",
            }
        }


    async def clear_logs(self) -> int:
        """一键清空所有历史日志记录并回收磁盘空间"""
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path, isolation_level=None) as db:
            cursor = await db.execute("DELETE FROM logs")
            rowcount = cursor.rowcount
            await db.execute("VACUUM")
            return rowcount

    async def cleanup_old_logs(self, max_entries: int = 20000, retention_days: int = 30) -> int:
        """
        滚动清理旧日志：
        1. 超过 max_entries 的最早数据淘汰；
        2. 超过 retention_days 天的数据清理。
        """
        await self._ensure_db()
        deleted_count = 0
        cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")

        async with aiosqlite.connect(self.db_path) as db:
            c1 = await db.execute("DELETE FROM logs WHERE created_at < ?", (cutoff_date,))
            deleted_count += c1.rowcount or 0

            c2 = await db.execute(
                """
                DELETE FROM logs
                WHERE id NOT IN (
                    SELECT id FROM logs ORDER BY id DESC LIMIT ?
                )
                """,
                (max_entries,),
            )
            deleted_count += c2.rowcount or 0
            await db.commit()

        return deleted_count


# 全局单例
log_service = LogService()
