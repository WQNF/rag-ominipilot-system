"""
SQL 执行器：封装 PostgreSQL 操作
支持：连接池、查询执行、错误重试、敏感字段脱敏
"""
import re
import time
from typing import List, Dict, Any, Optional
import psycopg2
from psycopg2 import sql, OperationalError


# 敏感字段列表（可配置）
SENSITIVE_COLUMNS = []


class SQLExecutor:
    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        user: str = "omnipilot",
        password: str = "omnipilot123",
        database: str = "omnipilot",
        max_retries: int = 2,
        retry_delay: float = 1.0,
        enable_desensitization: bool = True
    ):
        self.conn_params = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database
        }
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.enable_desensitization = enable_desensitization

    def _get_connection(self):
        """获取数据库连接"""
        return psycopg2.connect(**self.conn_params)

    def execute_query(self, query: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """
        执行查询并返回字典列表
        :param query: SQL 查询语句
        :param params: 查询参数（防注入）
        :return: 结果列表
        """
        last_exception = None
        for attempt in range(self.max_retries + 1):
            conn = None
            try:
                conn = self._get_connection()
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    if cur.description:  # 有返回结果
                        colnames = [desc[0] for desc in cur.description]
                        rows = cur.fetchall()
                        result = [dict(zip(colnames, row)) for row in rows]
                        # 脱敏处理
                        if self.enable_desensitization:
                            result = self._desensitize(colnames, result)
                        return result
                    else:
                        return []  # 无结果集（如 INSERT/UPDATE/DELETE）
            except (OperationalError, Exception) as e:
                last_exception = e
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                    continue
            finally:
                if conn:
                    conn.close()
        raise last_exception

    def _desensitize(self, colnames: List[str], rows: List[Dict]) -> List[Dict]:
        """对敏感字段进行脱敏"""
        sensitive_in_result = [col.lower() for col in colnames if col.lower() in SENSITIVE_COLUMNS]
        if not sensitive_in_result:
            return rows

        masked_rows = []
        for row in rows:
            masked_row = row.copy()
            for col in sensitive_in_result:
                if col in masked_row and masked_row[col] is not None:
                    masked_row[col] = "***已脱敏***"
            masked_rows.append(masked_row)
        return masked_rows

    def execute_batch(self, queries: List[str]) -> None:
        """批量执行多条语句（用于初始化）"""
        conn = self._get_connection()
        conn.autocommit = True
        with conn.cursor() as cur:
            for q in queries:
                cur.execute(q)
        conn.close()


# ========== 工具函数：包装给 Agent 使用 ==========

def format_query_results(results: List[Dict]) -> str:
    """将查询结果格式化为人类可读的字符串"""
    if not results:
        return "查询无结果。"
    # 简单表格形式
    headers = list(results[0].keys())
    header_line = " | ".join(headers)
    sep_line = "-" * len(header_line)
    lines = [header_line, sep_line]
    for row in results:
        lines.append(" | ".join(str(row[h]) for h in headers))
    return "\n".join(lines)


# 全局默认执行器（可后续从环境变量初始化）
default_executor = SQLExecutor()