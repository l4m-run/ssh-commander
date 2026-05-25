# -*- coding: utf-8 -*-
"""Менеджер подключений к базам данных через SSH-туннель.

Поддерживает PostgreSQL, MySQL и SQLite.
Для PostgreSQL/MySQL создаёт SSH-туннель и подключается через localhost.
Для SQLite открывает локальный файл напрямую.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.models.connection import Connection

logger = logging.getLogger(__name__)


class DbType(Enum):
    """Поддерживаемые типы СУБД."""
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SQLITE = "sqlite"


# Порты по умолчанию
DEFAULT_PORTS = {
    DbType.POSTGRESQL: 5432,
    DbType.MYSQL: 3306,
    DbType.SQLITE: 0,
}


@dataclass
class ColumnInfo:
    """Информация о колонке таблицы.

    Attributes:
        name: Имя колонки.
        type: Тип данных.
        nullable: Допускает NULL.
        is_pk: Является первичным ключом.
    """
    name: str = ""
    type: str = ""
    nullable: bool = True
    is_pk: bool = False


@dataclass
class QueryResult:
    """Результат SQL-запроса.

    Attributes:
        columns: Список имён колонок.
        rows: Список строк (кортежей).
        affected_rows: Количество затронутых строк (INSERT/UPDATE/DELETE).
        error: Текст ошибки (пустой при успехе).
        execution_time: Время выполнения в секундах.
    """
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    affected_rows: int = 0
    error: str = ""
    execution_time: float = 0.0

    @property
    def is_error(self) -> bool:
        """Есть ли ошибка."""
        return bool(self.error)

    @property
    def row_count(self) -> int:
        """Количество строк в результате."""
        return len(self.rows)

    def to_csv(self) -> str:
        """Экспорт результата в CSV."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(self.columns)
        for row in self.rows:
            writer.writerow(row)
        return output.getvalue()

    def to_json(self) -> str:
        """Экспорт результата в JSON."""
        data = []
        for row in self.rows:
            record = {}
            for i, col in enumerate(self.columns):
                val = row[i]
                # Конвертируем нестандартные типы в строку
                if isinstance(val, (bytes, bytearray)):
                    val = val.hex()
                elif not isinstance(val, (str, int, float, bool, type(None))):
                    val = str(val)
                record[col] = val
            data.append(record)
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)


class SSHTunnel:
    """SSH-туннель для доступа к БД через SSH.

    Реализация через paramiko Transport + socket forwarding.
    Создаёт локальный сокет, который проксирует данные
    к удалённому хосту через SSH-канал.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._server_socket: Any = None
        self._local_port: int = 0
        self._forward_thread: Any = None
        self._running = False

    @property
    def is_open(self) -> bool:
        """Туннель активен."""
        return self._running and self._client is not None

    @property
    def local_port(self) -> int:
        """Локальный порт туннеля."""
        return self._local_port

    def open(
        self,
        ssh_host: str,
        ssh_port: int,
        ssh_user: str,
        ssh_password: str = "",
        ssh_key_path: str = "",
        db_host: str = "localhost",
        db_port: int = 5432,
    ) -> int:
        """Открыть SSH-туннель.

        Args:
            ssh_host: Хост SSH-сервера.
            ssh_port: Порт SSH-сервера.
            ssh_user: Имя пользователя SSH.
            ssh_password: Пароль SSH.
            ssh_key_path: Путь к SSH-ключу.
            db_host: Хост БД на сервере (обычно localhost).
            db_port: Порт БД на сервере.

        Returns:
            Локальный порт для подключения к БД.
        """
        import paramiko
        import socket
        import select
        import threading

        # SSH-подключение
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict[str, Any] = {
            "hostname": ssh_host,
            "port": ssh_port,
            "username": ssh_user,
            "timeout": 10,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if ssh_key_path:
            connect_kwargs["key_filename"] = ssh_key_path
        elif ssh_password:
            connect_kwargs["password"] = ssh_password

        self._client.connect(**connect_kwargs)
        transport = self._client.get_transport()

        # Локальный сокет для прослушивания
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind(("127.0.0.1", 0))  # Случайный порт
        self._server_socket.listen(5)
        self._local_port = self._server_socket.getsockname()[1]
        self._running = True

        def _forward_connections() -> None:
            """Принимаем локальные соединения и проксируем через SSH."""
            while self._running:
                try:
                    if self._server_socket is None:
                        break
                    readable, _, _ = select.select(
                        [self._server_socket], [], [], 1.0,
                    )
                    if not readable:
                        continue
                    if self._server_socket is None:
                        break
                    local_conn, _ = self._server_socket.accept()
                except (OSError, ValueError, AttributeError):
                    break

                try:
                    channel = transport.open_channel(
                        "direct-tcpip",
                        (db_host, db_port),
                        local_conn.getpeername(),
                    )
                except Exception as e:
                    logger.error("Не удалось открыть SSH-канал: %s", e)
                    local_conn.close()
                    continue

                # Проксирование в отдельном потоке
                t = threading.Thread(
                    target=self._proxy,
                    args=(local_conn, channel),
                    daemon=True,
                )
                t.start()

        self._forward_thread = threading.Thread(
            target=_forward_connections, daemon=True,
        )
        self._forward_thread.start()

        logger.info(
            "SSH-туннель открыт: localhost:%d -> %s:%d (через %s:%d)",
            self._local_port, db_host, db_port, ssh_host, ssh_port,
        )
        return self._local_port

    @staticmethod
    def _proxy(local_conn: Any, channel: Any) -> None:
        """Проксирование данных между локальным сокетом и SSH-каналом."""
        import select
        try:
            while True:
                readable, _, _ = select.select([local_conn, channel], [], [], 1.0)
                if local_conn in readable:
                    data = local_conn.recv(65536)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in readable:
                    data = channel.recv(65536)
                    if not data:
                        break
                    local_conn.sendall(data)
        except Exception:
            pass
        finally:
            channel.close()
            local_conn.close()

    def close(self) -> None:
        """Закрыть SSH-туннель."""
        self._running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
            self._server_socket = None
        if self._forward_thread:
            self._forward_thread.join(timeout=2)
            self._forward_thread = None
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._local_port = 0
        logger.info("SSH-туннель закрыт")


class DatabaseConnection:
    """Абстракция над подключением к базе данных.

    Поддерживает PostgreSQL, MySQL, SQLite через единый интерфейс.
    """

    def __init__(self) -> None:
        self._conn: Any = None
        self._db_type: DbType | None = None
        self._tunnel = SSHTunnel()
        self._ssh_connection: Connection | None = None
        self._current_database: str = ""
        # Параметры подключения для переподключения
        self._connect_host: str = ""
        self._connect_port: int = 0
        self._db_user: str = ""
        self._db_password: str = ""

    @property
    def is_connected(self) -> bool:
        """Активно ли подключение."""
        return self._conn is not None

    @property
    def db_type(self) -> DbType | None:
        """Тип текущей СУБД."""
        return self._db_type

    @property
    def current_database(self) -> str:
        """Текущая база данных."""
        return self._current_database

    def connect(
        self,
        db_type: DbType,
        db_host: str = "localhost",
        db_port: int = 0,
        db_user: str = "",
        db_password: str = "",
        database: str = "",
        ssh_connection: Connection | None = None,
        ssh_password: str = "",
    ) -> None:
        """Подключиться к базе данных.

        Args:
            db_type: Тип СУБД.
            db_host: Хост БД (на сервере, не localhost клиента).
            db_port: Порт БД (0 = по умолчанию).
            db_user: Пользователь БД.
            db_password: Пароль БД.
            database: Имя базы данных.
            ssh_connection: SSH-подключение для туннеля (None = без туннеля).
            ssh_password: Расшифрованный пароль SSH.
        """
        self.disconnect()
        self._db_type = db_type

        if db_port == 0:
            db_port = DEFAULT_PORTS.get(db_type, 0)

        connect_host = db_host
        connect_port = db_port

        # SSH-туннель (не для SQLite)
        if ssh_connection and db_type != DbType.SQLITE:
            self._ssh_connection = ssh_connection
            connect_port = self._tunnel.open(
                ssh_host=ssh_connection.host,
                ssh_port=ssh_connection.port,
                ssh_user=ssh_connection.username,
                ssh_password=ssh_password,
                ssh_key_path=ssh_connection.ssh_key_path,
                db_host=db_host,
                db_port=db_port,
            )
            connect_host = "127.0.0.1"

        # Подключение к БД
        self._connect_host = connect_host
        self._connect_port = connect_port
        self._db_user = db_user
        self._db_password = db_password

        if db_type == DbType.POSTGRESQL:
            self._connect_pg(connect_host, connect_port, db_user, db_password, database)
        elif db_type == DbType.MYSQL:
            self._connect_mysql(connect_host, connect_port, db_user, db_password, database)
        elif db_type == DbType.SQLITE:
            self._connect_sqlite(database)

        self._current_database = database
        logger.info("Подключено к %s: %s", db_type.value, database or "(default)")

    def _connect_pg(
        self, host: str, port: int, user: str, password: str, database: str,
    ) -> None:
        """Подключение к PostgreSQL."""
        import psycopg2
        self._conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database or "postgres",
            connect_timeout=10,
        )
        self._conn.autocommit = True

    def _connect_mysql(
        self, host: str, port: int, user: str, password: str, database: str,
    ) -> None:
        """Подключение к MySQL."""
        import pymysql
        self._conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database or None,
            connect_timeout=10,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.Cursor,
        )
        self._conn.autocommit(True)

    def _connect_sqlite(self, path: str) -> None:
        """Подключение к SQLite."""
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = None

    def disconnect(self) -> None:
        """Закрыть подключение и туннель."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        self._tunnel.close()
        self._db_type = None
        self._ssh_connection = None
        self._current_database = ""

    def switch_database(self, database: str) -> None:
        """Переключиться на другую базу данных.

        Args:
            database: Имя базы данных.
        """
        if self._db_type == DbType.POSTGRESQL:
            # PostgreSQL требует переподключения для смены БД
            self._conn.close()
            self._connect_pg(
                self._connect_host, self._connect_port,
                self._db_user, self._db_password, database,
            )
        elif self._db_type == DbType.MYSQL:
            self._conn.select_db(database)
        # SQLite не поддерживает переключение
        self._current_database = database

    # --- Метаданные ---

    def list_databases(self) -> list[str]:
        """Получить список баз данных."""
        if self._db_type == DbType.POSTGRESQL:
            result = self.execute_query(
                "SELECT datname FROM pg_database "
                "WHERE datistemplate = false ORDER BY datname"
            )
            return [row[0] for row in result.rows]

        if self._db_type == DbType.MYSQL:
            result = self.execute_query("SHOW DATABASES")
            # Фильтруем системные БД
            system_dbs = {"information_schema", "performance_schema", "mysql", "sys"}
            return [row[0] for row in result.rows if row[0] not in system_dbs]

        if self._db_type == DbType.SQLITE:
            return [self._current_database or "main"]

        return []

    def list_tables(self, database: str = "") -> list[str]:
        """Получить список таблиц.

        Args:
            database: Имя БД (пустое = текущая).
        """
        if database and database != self._current_database:
            self.switch_database(database)

        if self._db_type == DbType.POSTGRESQL:
            result = self.execute_query(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' ORDER BY tablename"
            )
            return [row[0] for row in result.rows]

        if self._db_type == DbType.MYSQL:
            result = self.execute_query("SHOW TABLES")
            return [row[0] for row in result.rows]

        if self._db_type == DbType.SQLITE:
            result = self.execute_query(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
            return [row[0] for row in result.rows]

        return []

    def get_columns(self, table: str) -> list[ColumnInfo]:
        """Получить информацию о колонках таблицы.

        Args:
            table: Имя таблицы.
        """
        if self._db_type == DbType.POSTGRESQL:
            return self._get_columns_pg(table)
        if self._db_type == DbType.MYSQL:
            return self._get_columns_mysql(table)
        if self._db_type == DbType.SQLITE:
            return self._get_columns_sqlite(table)
        return []

    def _get_columns_pg(self, table: str) -> list[ColumnInfo]:
        """Колонки PostgreSQL."""
        # Получаем колонки
        cols_result = self.execute_query(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            f"WHERE table_name = '{table}' AND table_schema = 'public' "
            "ORDER BY ordinal_position"
        )
        # Получаем PK
        pk_result = self.execute_query(
            "SELECT a.attname "
            "FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid "
            "  AND a.attnum = ANY(i.indkey) "
            f"WHERE i.indrelid = '{table}'::regclass AND i.indisprimary"
        )
        pk_names = {row[0] for row in pk_result.rows}

        columns = []
        for row in cols_result.rows:
            columns.append(ColumnInfo(
                name=row[0],
                type=row[1],
                nullable=row[2] == "YES",
                is_pk=row[0] in pk_names,
            ))
        return columns

    def _get_columns_mysql(self, table: str) -> list[ColumnInfo]:
        """Колонки MySQL."""
        result = self.execute_query(f"SHOW COLUMNS FROM `{table}`")
        columns = []
        for row in result.rows:
            columns.append(ColumnInfo(
                name=row[0],
                type=row[1],
                nullable=row[2] == "YES",
                is_pk=row[3] == "PRI",
            ))
        return columns

    def _get_columns_sqlite(self, table: str) -> list[ColumnInfo]:
        """Колонки SQLite."""
        result = self.execute_query(f"PRAGMA table_info('{table}')")
        columns = []
        for row in result.rows:
            columns.append(ColumnInfo(
                name=row[1],
                type=row[2],
                nullable=row[3] == 0,
                is_pk=row[5] > 0,
            ))
        return columns

    def get_primary_keys(self, table: str) -> list[str]:
        """Получить список колонок первичного ключа.

        Args:
            table: Имя таблицы.
        """
        columns = self.get_columns(table)
        return [c.name for c in columns if c.is_pk]

    # --- Данные ---

    def fetch_rows(
        self,
        table: str,
        limit: int = 100,
        offset: int = 0,
        order_by: str = "",
        order_desc: bool = False,
    ) -> QueryResult:
        """Получить строки таблицы с пагинацией.

        Args:
            table: Имя таблицы.
            limit: Максимум строк.
            offset: Смещение.
            order_by: Колонка сортировки.
            order_desc: Обратная сортировка.
        """
        # Экранируем имя таблицы
        tbl = self._quote_table(table)

        sql = f"SELECT * FROM {tbl}"
        if order_by:
            direction = "DESC" if order_desc else "ASC"
            col = self._quote_column(order_by)
            sql += f" ORDER BY {col} {direction}"
        sql += f" LIMIT {limit} OFFSET {offset}"

        return self.execute_query(sql)

    def count_rows(self, table: str) -> int:
        """Получить общее количество строк в таблице."""
        tbl = self._quote_table(table)
        result = self.execute_query(f"SELECT COUNT(*) FROM {tbl}")
        if result.rows:
            return result.rows[0][0]
        return 0

    def update_cell(
        self,
        table: str,
        pk_columns: list[str],
        pk_values: list[Any],
        column: str,
        new_value: Any,
    ) -> QueryResult:
        """Обновить значение ячейки.

        Args:
            table: Имя таблицы.
            pk_columns: Колонки первичного ключа.
            pk_values: Значения первичного ключа.
            column: Колонка для обновления.
            new_value: Новое значение.
        """
        tbl = self._quote_table(table)
        col = self._quote_column(column)

        # WHERE по PK
        where_parts = []
        for pk_col, pk_val in zip(pk_columns, pk_values):
            qcol = self._quote_column(pk_col)
            if pk_val is None:
                where_parts.append(f"{qcol} IS NULL")
            else:
                where_parts.append(f"{qcol} = {self._quote_value(pk_val)}")

        where_clause = " AND ".join(where_parts)
        value = self._quote_value(new_value)

        sql = f"UPDATE {tbl} SET {col} = {value} WHERE {where_clause}"
        return self.execute_query(sql)

    # --- SQL ---

    def execute_query(self, sql: str) -> QueryResult:
        """Выполнить произвольный SQL-запрос.

        Args:
            sql: SQL-запрос.
        """
        if not self._conn:
            return QueryResult(error="Нет подключения к БД")

        start_time = time.time()
        try:
            cursor = self._conn.cursor()
            cursor.execute(sql)

            elapsed = time.time() - start_time

            # Определяем тип запроса
            sql_upper = sql.strip().upper()
            if sql_upper.startswith(("SELECT", "SHOW", "DESCRIBE", "EXPLAIN", "PRAGMA")):
                # Запрос с результатом
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = cursor.fetchall()
                return QueryResult(
                    columns=columns,
                    rows=[tuple(row) for row in rows],
                    execution_time=elapsed,
                )
            else:
                # DML запрос
                affected = cursor.rowcount
                # Для MySQL нужен явный commit при autocommit=False
                if hasattr(self._conn, 'commit'):
                    try:
                        self._conn.commit()
                    except Exception:
                        pass
                return QueryResult(
                    affected_rows=affected,
                    execution_time=elapsed,
                )
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error("Ошибка SQL: %s", e)
            return QueryResult(error=str(e), execution_time=elapsed)

    # --- Вспомогательные ---

    def _quote_table(self, name: str) -> str:
        """Экранировать имя таблицы."""
        if self._db_type == DbType.MYSQL:
            return f"`{name}`"
        return f'"{name}"'

    def _quote_column(self, name: str) -> str:
        """Экранировать имя колонки."""
        if self._db_type == DbType.MYSQL:
            return f"`{name}`"
        return f'"{name}"'

    @staticmethod
    def _quote_value(value: Any) -> str:
        """Экранировать значение для SQL."""
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return str(value)
        # Строка: экранируем кавычки
        escaped = str(value).replace("'", "''")
        return f"'{escaped}'"
