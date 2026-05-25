# -*- coding: utf-8 -*-
"""Работа с SQLite базой данных.

Хранит подключения и сохранённые команды.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.models.command import SavedCommand
from src.models.connection import Connection
from src.models.db_connection import DbConnectionConfig
from src.models.secret import SecretEntry


class Database:
    """Обёртка над SQLite для хранения данных приложения.

    Attributes:
        _db_path: Путь к файлу БД.
        _conn: Соединение с БД.
    """

    # Текущая версия схемы
    SCHEMA_VERSION = 3

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        """Открыть соединение с БД и применить миграции."""
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        """Закрыть соединение с БД."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Получить активное соединение."""
        if self._conn is None:
            raise RuntimeError("БД не подключена. Вызовите connect() сначала.")
        return self._conn

    def _migrate(self) -> None:
        """Применить миграции схемы."""
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)"
        )
        row = self.conn.execute("SELECT version FROM schema_version").fetchone()
        current_version = row["version"] if row else 0

        if current_version < 1:
            self._migrate_v1()
        if current_version < 2:
            self._migrate_v2()
        if current_version < 3:
            self._migrate_v3()

        if not row:
            self.conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (self.SCHEMA_VERSION,),
            )
        else:
            self.conn.execute(
                "UPDATE schema_version SET version = ?", (self.SCHEMA_VERSION,)
            )
        self.conn.commit()

    def _migrate_v1(self) -> None:
        """Миграция v1: создание начальных таблиц."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 22,
                username TEXT NOT NULL DEFAULT '',
                encrypted_password TEXT NOT NULL DEFAULT '',
                ssh_key_path TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                last_used TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                command_text TEXT NOT NULL DEFAULT '',
                connection_id INTEGER,
                category TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (connection_id) REFERENCES connections(id) ON DELETE SET NULL
            );
        """)

    def _migrate_v2(self) -> None:
        """Миграция v2: таблица подключений к БД."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS db_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                ssh_connection_id INTEGER,
                db_type TEXT NOT NULL DEFAULT 'postgresql',
                db_host TEXT NOT NULL DEFAULT 'localhost',
                db_port INTEGER NOT NULL DEFAULT 5432,
                db_user TEXT NOT NULL DEFAULT '',
                encrypted_db_password TEXT NOT NULL DEFAULT '',
                database_name TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (ssh_connection_id)
                    REFERENCES connections(id) ON DELETE SET NULL
            );
        """)

    def _migrate_v3(self) -> None:
        """Миграция v3: таблица секретов."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS secrets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                encrypted_password TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
        """)

    # --- Подключения ---

    def get_all_connections(self) -> list[Connection]:
        """Получить все подключения."""
        rows = self.conn.execute(
            "SELECT * FROM connections ORDER BY group_name, name"
        ).fetchall()
        return [Connection.from_dict(dict(row)) for row in rows]

    def get_connection(self, conn_id: int) -> Connection | None:
        """Получить подключение по ID."""
        row = self.conn.execute(
            "SELECT * FROM connections WHERE id = ?", (conn_id,)
        ).fetchone()
        return Connection.from_dict(dict(row)) if row else None

    def save_connection(self, connection: Connection) -> int:
        """Сохранить подключение (создать или обновить).

        Args:
            connection: Объект подключения.

        Returns:
            ID сохранённого подключения.
        """
        data = connection.to_dict()
        if connection.id is None:
            # Создание
            cursor = self.conn.execute(
                """INSERT INTO connections
                   (name, host, port, username, encrypted_password,
                    ssh_key_path, group_name, created_at, last_used)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["name"], data["host"], data["port"],
                    data["username"], data["encrypted_password"],
                    data["ssh_key_path"], data["group_name"],
                    data["created_at"], data["last_used"],
                ),
            )
            self.conn.commit()
            return cursor.lastrowid  # type: ignore[return-value]
        # Обновление
        self.conn.execute(
            """UPDATE connections SET
               name=?, host=?, port=?, username=?, encrypted_password=?,
               ssh_key_path=?, group_name=?, last_used=?
               WHERE id=?""",
            (
                data["name"], data["host"], data["port"],
                data["username"], data["encrypted_password"],
                data["ssh_key_path"], data["group_name"],
                data["last_used"], data["id"],
            ),
        )
        self.conn.commit()
        return connection.id

    def delete_connection(self, conn_id: int) -> None:
        """Удалить подключение по ID."""
        self.conn.execute("DELETE FROM connections WHERE id = ?", (conn_id,))
        self.conn.commit()

    def update_last_used(self, conn_id: int, timestamp: str) -> None:
        """Обновить время последнего использования."""
        self.conn.execute(
            "UPDATE connections SET last_used = ? WHERE id = ?",
            (timestamp, conn_id),
        )
        self.conn.commit()

    # --- Команды ---

    def get_all_commands(self) -> list[SavedCommand]:
        """Получить все сохранённые команды."""
        rows = self.conn.execute(
            "SELECT * FROM commands ORDER BY category, sort_order, name"
        ).fetchall()
        return [SavedCommand.from_dict(dict(row)) for row in rows]

    def get_commands_for_connection(self, conn_id: int | None) -> list[SavedCommand]:
        """Получить команды для конкретного подключения + глобальные.

        Args:
            conn_id: ID подключения (None = только глобальные).
        """
        if conn_id is None:
            rows = self.conn.execute(
                "SELECT * FROM commands WHERE connection_id IS NULL "
                "ORDER BY category, sort_order, name"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM commands "
                "WHERE connection_id IS NULL OR connection_id = ? "
                "ORDER BY category, sort_order, name",
                (conn_id,),
            ).fetchall()
        return [SavedCommand.from_dict(dict(row)) for row in rows]

    def save_command(self, command: SavedCommand) -> int:
        """Сохранить команду (создать или обновить).

        Returns:
            ID сохранённой команды.
        """
        data = command.to_dict()
        if command.id is None:
            cursor = self.conn.execute(
                """INSERT INTO commands
                   (name, command_text, connection_id, category, sort_order)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    data["name"], data["command_text"],
                    data["connection_id"], data["category"],
                    data["sort_order"],
                ),
            )
            self.conn.commit()
            return cursor.lastrowid  # type: ignore[return-value]
        self.conn.execute(
            """UPDATE commands SET
               name=?, command_text=?, connection_id=?, category=?, sort_order=?
               WHERE id=?""",
            (
                data["name"], data["command_text"],
                data["connection_id"], data["category"],
                data["sort_order"], data["id"],
            ),
        )
        self.conn.commit()
        return command.id

    def delete_command(self, cmd_id: int) -> None:
        """Удалить команду по ID."""
        self.conn.execute("DELETE FROM commands WHERE id = ?", (cmd_id,))
        self.conn.commit()

    def update_password(self, conn_id: int, encrypted_password: str) -> None:
        """Обновить зашифрованный пароль подключения.

        Args:
            conn_id: ID подключения.
            encrypted_password: Новый зашифрованный пароль.
        """
        self.conn.execute(
            "UPDATE connections SET encrypted_password = ? WHERE id = ?",
            (encrypted_password, conn_id),
        )
        self.conn.commit()

    # --- Подключения к БД ---

    def get_all_db_connections(self) -> list[DbConnectionConfig]:
        """Получить все подключения к БД."""
        rows = self.conn.execute(
            "SELECT * FROM db_connections ORDER BY name"
        ).fetchall()
        return [DbConnectionConfig.from_dict(dict(row)) for row in rows]

    def get_db_connection(self, db_conn_id: int) -> DbConnectionConfig | None:
        """Получить подключение к БД по ID."""
        row = self.conn.execute(
            "SELECT * FROM db_connections WHERE id = ?", (db_conn_id,)
        ).fetchone()
        return DbConnectionConfig.from_dict(dict(row)) if row else None

    def save_db_connection(self, db_conn: DbConnectionConfig) -> int:
        """Сохранить подключение к БД (создать или обновить).

        Returns:
            ID сохранённого подключения.
        """
        data = db_conn.to_dict()
        if db_conn.id is None:
            cursor = self.conn.execute(
                """INSERT INTO db_connections
                   (name, ssh_connection_id, db_type, db_host, db_port,
                    db_user, encrypted_db_password, database_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["name"], data["ssh_connection_id"],
                    data["db_type"], data["db_host"], data["db_port"],
                    data["db_user"], data["encrypted_db_password"],
                    data["database_name"],
                ),
            )
            self.conn.commit()
            return cursor.lastrowid  # type: ignore[return-value]
        self.conn.execute(
            """UPDATE db_connections SET
               name=?, ssh_connection_id=?, db_type=?, db_host=?,
               db_port=?, db_user=?, encrypted_db_password=?, database_name=?
               WHERE id=?""",
            (
                data["name"], data["ssh_connection_id"],
                data["db_type"], data["db_host"], data["db_port"],
                data["db_user"], data["encrypted_db_password"],
                data["database_name"], data["id"],
            ),
        )
        self.conn.commit()
        return db_conn.id

    def delete_db_connection(self, db_conn_id: int) -> None:
        """Удалить подключение к БД по ID."""
        self.conn.execute(
            "DELETE FROM db_connections WHERE id = ?", (db_conn_id,)
        )
        self.conn.commit()

    # --- Секреты ---

    def get_all_secrets(self) -> list[SecretEntry]:
        """Получить все секреты."""
        rows = self.conn.execute(
            "SELECT * FROM secrets ORDER BY category, name"
        ).fetchall()
        return [SecretEntry.from_dict(dict(row)) for row in rows]

    def get_secret(self, secret_id: int) -> SecretEntry | None:
        """Получить секрет по ID."""
        row = self.conn.execute(
            "SELECT * FROM secrets WHERE id = ?", (secret_id,)
        ).fetchone()
        return SecretEntry.from_dict(dict(row)) if row else None

    def save_secret(self, secret: SecretEntry) -> int:
        """Сохранить секрет (создать или обновить).

        Returns:
            ID сохранённого секрета.
        """
        from datetime import datetime
        now = datetime.now().isoformat()

        if secret.id is None:
            cursor = self.conn.execute(
                """INSERT INTO secrets
                   (name, username, encrypted_password, url, notes,
                    category, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    secret.name, secret.username,
                    secret.encrypted_password, secret.url,
                    secret.notes, secret.category, now, now,
                ),
            )
            self.conn.commit()
            return cursor.lastrowid  # type: ignore[return-value]

        self.conn.execute(
            """UPDATE secrets SET
               name=?, username=?, encrypted_password=?, url=?,
               notes=?, category=?, updated_at=?
               WHERE id=?""",
            (
                secret.name, secret.username,
                secret.encrypted_password, secret.url,
                secret.notes, secret.category, now, secret.id,
            ),
        )
        self.conn.commit()
        return secret.id

    def delete_secret(self, secret_id: int) -> None:
        """Удалить секрет по ID."""
        self.conn.execute("DELETE FROM secrets WHERE id = ?", (secret_id,))
        self.conn.commit()
