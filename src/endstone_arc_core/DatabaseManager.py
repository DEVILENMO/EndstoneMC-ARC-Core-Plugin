import sqlite3
from typing import Any, List, Dict, Optional, Union
import threading
from pathlib import Path


class DatabaseManager:
    def __init__(self, db_path: str):
        """
        初始化数据库管理器
        :param db_path: 数据库文件路径
        """
        self.db_path = db_path
        self._local = threading.local()  # 线程本地存储
        self._ensure_db_exists()

    def _ensure_db_exists(self):
        """确保数据库文件存在"""
        db_file = Path(self.db_path)
        if not db_file.parent.exists():
            db_file.parent.mkdir(parents=True)

    @property
    def connection(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接"""
        if not hasattr(self._local, 'connection'):
            self._local.connection = sqlite3.connect(self.db_path)
            # 设置行工厂为字典类型
            self._local.connection.row_factory = sqlite3.Row
        return self._local.connection

    def close(self):
        """关闭当前线程的数据库连接"""
        if hasattr(self._local, 'connection'):
            self._local.connection.close()
            delattr(self._local, 'connection')

    def execute(self, sql: str, params: tuple = ()) -> bool:
        """
        执行SQL语句
        :param sql: SQL语句
        :param params: SQL参数
        :return: 是否执行成功
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute(sql, params)
            self.connection.commit()
            return True
        except Exception as e:
            print(f"Execute SQL error: {str(e)}")
            self.connection.rollback()
            return False

    def query_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        """
        查询单条记录
        :param sql: SQL语句
        :param params: SQL参数
        :return: 查询结果字典或None
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            print(f"Query one error: {str(e)}")
            return None

    def query_all(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """
        查询多条记录
        :param sql: SQL语句
        :param params: SQL参数
        :return: 查询结果列表
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            print(f"Query all error: {str(e)}")
            return []

    def insert(self, table: str, data: Dict[str, Any]) -> bool:
        """
        插入数据
        :param table: 表名
        :param data: 要插入的数据字典
        :return: 是否插入成功
        """
        fields = ','.join(data.keys())
        placeholders = ','.join(['?' for _ in data])
        sql = f"INSERT INTO {table} ({fields}) VALUES ({placeholders})"
        return self.execute(sql, tuple(data.values()))

    def update(self, table: str, data: Dict[str, Any], where: str, params: tuple = ()) -> bool:
        """
        更新数据
        :param table: 表名
        :param data: 要更新的数据字典
        :param where: WHERE子句
        :param params: WHERE子句的参数
        :return: 是否更新成功
        """
        set_clause = ','.join([f"{k}=?" for k in data.keys()])
        sql = f"UPDATE {table} SET {set_clause} WHERE {where}"
        return self.execute(sql, tuple(data.values()) + params)

    def delete(self, table: str, where: str, params: tuple = ()) -> bool:
        """
        删除数据
        :param table: 表名
        :param where: WHERE子句
        :param params: WHERE子句的参数
        :return: 是否删除成功
        """
        sql = f"DELETE FROM {table} WHERE {where}"
        return self.execute(sql, params)

    def create_table(self, table: str, fields: Dict[str, str]) -> bool:
        """
        创建表
        :param table: 表名
        :param fields: 字段定义字典，key为字段名，value为字段类型定义
        :return: 是否创建成功
        """
        field_defs = ','.join([f"{k} {v}" for k, v in fields.items()])
        sql = f"CREATE TABLE IF NOT EXISTS {table} ({field_defs})"
        return self.execute(sql)

    def table_exists(self, table: str) -> bool:
        """
        检查表是否存在
        :param table: 表名
        :return: 表是否存在
        """
        sql = "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
        return self.query_one(sql, (table,)) is not None