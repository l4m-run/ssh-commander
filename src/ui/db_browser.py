# -*- coding: utf-8 -*-
"""Браузер баз данных через SSH-туннель.

Основной виджет вкладки "Базы данных":
- Форма подключения (сервер, тип БД, креды)
- Дерево баз/таблиц
- Таблица данных с пагинацией
- SQL-редактор
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.crypto import crypto
from src.core.db_manager import DatabaseConnection, DbType, DEFAULT_PORTS
from src.ui.db_table_view import DbTableView

if TYPE_CHECKING:
    from src.core.database import Database
    from src.models.connection import Connection

logger = logging.getLogger(__name__)


class DatabaseBrowser(QWidget):
    """Браузер баз данных.

    Layout:
    ┌─────────────────────────────────────────────────────────┐
    │  Подключение: [сервер] [тип] [хост:порт] [user] [pass] │
    ├───────────┬─────────────────────────────────────────────┤
    │  Дерево   │  Данные таблицы / результат запроса         │
    │  БД/табл  │                                             │
    ├───────────┴─────────────────────────────────────────────┤
    │  SQL: [____________________________________] [Выполнить]│
    └─────────────────────────────────────────────────────────┘
    """

    def __init__(
        self,
        app_db: Database,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_db = app_db
        self._db_conn = DatabaseConnection()
        self._connections: list[Connection] = []

        self._setup_ui()
        self._refresh_connections()

    def _setup_ui(self) -> None:
        """Создание UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Форма подключения
        self._setup_connection_form(layout)

        # Основная область: дерево + данные
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Дерево БД/таблиц
        tree_widget = QWidget()
        tree_layout = QVBoxLayout(tree_widget)
        tree_layout.setContentsMargins(0, 0, 0, 0)

        tree_label = QLabel("Базы данных")
        tree_label.setStyleSheet("font-weight: bold; padding: 2px;")
        tree_layout.addWidget(tree_label)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(True)
        self._tree.itemDoubleClicked.connect(self._on_tree_double_click)
        self._tree.itemClicked.connect(self._on_tree_click)
        tree_layout.addWidget(self._tree)

        splitter.addWidget(tree_widget)

        # Таблица данных
        self._table_view = DbTableView(self._db_conn)
        splitter.addWidget(self._table_view)

        splitter.setSizes([200, 600])
        layout.addWidget(splitter, stretch=1)

        # SQL-редактор
        self._setup_sql_editor(layout)

    def _setup_connection_form(self, parent_layout: QVBoxLayout) -> None:
        """Создание формы подключения."""
        group = QGroupBox("Подключение к базе данных")
        form_layout = QVBoxLayout(group)

        # Первая строка: сервер + тип БД
        row1 = QHBoxLayout()

        row1.addWidget(QLabel("SSH-сервер:"))
        self._server_combo = QComboBox()
        self._server_combo.setMinimumWidth(200)
        row1.addWidget(self._server_combo)

        row1.addWidget(QLabel("Тип БД:"))
        self._type_combo = QComboBox()
        self._type_combo.addItem("PostgreSQL", DbType.POSTGRESQL)
        self._type_combo.addItem("MySQL", DbType.MYSQL)
        self._type_combo.addItem("SQLite (локальный)", DbType.SQLITE)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        row1.addWidget(self._type_combo)

        row1.addStretch()
        form_layout.addLayout(row1)

        # Вторая строка: параметры БД
        row2 = QHBoxLayout()

        row2.addWidget(QLabel("Хост БД:"))
        self._db_host_edit = QLineEdit("localhost")
        self._db_host_edit.setMaximumWidth(150)
        row2.addWidget(self._db_host_edit)

        row2.addWidget(QLabel("Порт:"))
        self._db_port_edit = QLineEdit("5432")
        self._db_port_edit.setMaximumWidth(70)
        row2.addWidget(self._db_port_edit)

        row2.addWidget(QLabel("Пользователь:"))
        self._db_user_edit = QLineEdit()
        self._db_user_edit.setMaximumWidth(120)
        row2.addWidget(self._db_user_edit)

        row2.addWidget(QLabel("Пароль:"))
        self._db_pass_edit = QLineEdit()
        self._db_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._db_pass_edit.setMaximumWidth(120)
        row2.addWidget(self._db_pass_edit)

        row2.addWidget(QLabel("База:"))
        self._db_name_edit = QLineEdit()
        self._db_name_edit.setMaximumWidth(120)
        self._db_name_edit.setPlaceholderText("(все)")
        row2.addWidget(self._db_name_edit)

        row2.addStretch()
        form_layout.addLayout(row2)

        # SQLite: поле пути к файлу (скрыто по умолчанию)
        self._sqlite_row = QHBoxLayout()
        self._sqlite_row_widget = QWidget()
        sqlite_inner = QHBoxLayout(self._sqlite_row_widget)
        sqlite_inner.setContentsMargins(0, 0, 0, 0)

        sqlite_inner.addWidget(QLabel("Файл SQLite:"))
        self._sqlite_path_edit = QLineEdit()
        self._sqlite_path_edit.setPlaceholderText("/path/to/database.db")
        sqlite_inner.addWidget(self._sqlite_path_edit, stretch=1)

        browse_btn = QPushButton("Обзор...")
        browse_btn.clicked.connect(self._browse_sqlite)
        sqlite_inner.addWidget(browse_btn)

        self._sqlite_row_widget.setVisible(False)
        form_layout.addWidget(self._sqlite_row_widget)

        # Третья строка: кнопки
        row3 = QHBoxLayout()

        btn_style = (
            "QPushButton { background: #3B82F6; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #2563EB; }"
            "QPushButton:disabled { background: #93C5FD; }"
        )
        btn_style_secondary = (
            "QPushButton { background: #E5E5E7; color: #18181B;"
            " border: 1px solid #D4D4D8; border-radius: 4px;"
            " padding: 6px 16px; }"
            "QPushButton:hover { background: #D4D4D8; }"
        )

        self._connect_btn = QPushButton("Подключиться")
        self._connect_btn.setStyleSheet(btn_style)
        self._connect_btn.clicked.connect(self._connect)
        row3.addWidget(self._connect_btn)

        self._disconnect_btn = QPushButton("Отключиться")
        self._disconnect_btn.setStyleSheet(btn_style_secondary)
        self._disconnect_btn.clicked.connect(self._disconnect)
        self._disconnect_btn.setEnabled(False)
        row3.addWidget(self._disconnect_btn)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #71717A; padding-left: 8px;")
        row3.addWidget(self._status_label, stretch=1)

        form_layout.addLayout(row3)
        parent_layout.addWidget(group)

    def _setup_sql_editor(self, parent_layout: QVBoxLayout) -> None:
        """Создание SQL-редактора."""
        group = QGroupBox("SQL-запрос")
        sql_layout = QVBoxLayout(group)

        self._sql_edit = QPlainTextEdit()
        self._sql_edit.setPlaceholderText(
            "SELECT * FROM users WHERE id > 5 ORDER BY name..."
        )
        self._sql_edit.setMaximumHeight(100)
        self._sql_edit.setStyleSheet(
            "QPlainTextEdit { font-family: monospace; font-size: 13px; }"
        )
        sql_layout.addWidget(self._sql_edit)

        btn_row = QHBoxLayout()

        btn_style = (
            "QPushButton { background: #10B981; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #059669; }"
            "QPushButton:disabled { background: #6EE7B7; }"
        )
        btn_style_secondary = (
            "QPushButton { background: #E5E5E7; color: #18181B;"
            " border: 1px solid #D4D4D8; border-radius: 4px;"
            " padding: 6px 12px; }"
            "QPushButton:hover { background: #D4D4D8; }"
        )

        self._exec_btn = QPushButton("Выполнить (Ctrl+Enter)")
        self._exec_btn.setStyleSheet(btn_style)
        self._exec_btn.clicked.connect(self._execute_sql)
        self._exec_btn.setEnabled(False)
        btn_row.addWidget(self._exec_btn)

        # Экспорт результатов
        self._export_csv_btn = QPushButton("Экспорт CSV")
        self._export_csv_btn.setStyleSheet(btn_style_secondary)
        self._export_csv_btn.clicked.connect(lambda: self._export_result("csv"))
        self._export_csv_btn.setEnabled(False)
        btn_row.addWidget(self._export_csv_btn)

        self._export_json_btn = QPushButton("Экспорт JSON")
        self._export_json_btn.setStyleSheet(btn_style_secondary)
        self._export_json_btn.clicked.connect(lambda: self._export_result("json"))
        self._export_json_btn.setEnabled(False)
        btn_row.addWidget(self._export_json_btn)

        self._sql_status = QLabel("")
        self._sql_status.setStyleSheet("color: #71717A; padding-left: 8px;")
        btn_row.addWidget(self._sql_status, stretch=1)

        sql_layout.addLayout(btn_row)
        parent_layout.addWidget(group)

        # Ctrl+Enter для выполнения
        from PySide6.QtGui import QShortcut, QKeySequence
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self._sql_edit)
        shortcut.activated.connect(self._execute_sql)

    # --- Подключение ---

    def _refresh_connections(self) -> None:
        """Обновить список SSH-подключений."""
        self._server_combo.clear()
        self._server_combo.addItem("(без SSH-туннеля)", None)
        self._connections = self._app_db.get_all_connections()
        for conn in self._connections:
            self._server_combo.addItem(
                f"{conn.display_name} ({conn.host}:{conn.port})",
                conn.id,
            )

    def _on_type_changed(self, index: int) -> None:
        """Смена типа БД - обновить порт и видимость полей."""
        db_type = self._type_combo.currentData()
        if db_type == DbType.SQLITE:
            self._db_host_edit.setEnabled(False)
            self._db_port_edit.setEnabled(False)
            self._db_user_edit.setEnabled(False)
            self._db_pass_edit.setEnabled(False)
            self._db_name_edit.setEnabled(False)
            self._server_combo.setEnabled(False)
            self._sqlite_row_widget.setVisible(True)
        else:
            self._db_host_edit.setEnabled(True)
            self._db_port_edit.setEnabled(True)
            self._db_user_edit.setEnabled(True)
            self._db_pass_edit.setEnabled(True)
            self._db_name_edit.setEnabled(True)
            self._server_combo.setEnabled(True)
            self._sqlite_row_widget.setVisible(False)

            default_port = DEFAULT_PORTS.get(db_type, 5432)
            self._db_port_edit.setText(str(default_port))

    def _browse_sqlite(self) -> None:
        """Выбрать файл SQLite."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Выбрать файл SQLite",
            "", "SQLite (*.db *.sqlite *.sqlite3);;Все файлы (*)",
        )
        if path:
            self._sqlite_path_edit.setText(path)

    def _connect(self) -> None:
        """Подключиться к базе данных."""
        db_type = self._type_combo.currentData()
        if db_type is None:
            return

        try:
            self._status_label.setText("Подключение...")
            self._connect_btn.setEnabled(False)

            # SSH-подключение
            ssh_conn = None
            ssh_password = ""
            conn_id = self._server_combo.currentData()
            if conn_id is not None and db_type != DbType.SQLITE:
                ssh_conn = self._app_db.get_connection(conn_id)
                if ssh_conn and ssh_conn.encrypted_password:
                    ssh_password = crypto.decrypt(ssh_conn.encrypted_password)

            # Параметры БД
            if db_type == DbType.SQLITE:
                database = self._sqlite_path_edit.text().strip()
                if not database:
                    raise ValueError("Укажите путь к файлу SQLite")
                self._db_conn.connect(
                    db_type=db_type,
                    database=database,
                )
            else:
                db_host = self._db_host_edit.text().strip() or "localhost"
                db_port = int(self._db_port_edit.text().strip() or "0")
                db_user = self._db_user_edit.text().strip()
                db_password = self._db_pass_edit.text().strip()
                database = self._db_name_edit.text().strip()

                self._db_conn.connect(
                    db_type=db_type,
                    db_host=db_host,
                    db_port=db_port,
                    db_user=db_user,
                    db_password=db_password,
                    database=database,
                    ssh_connection=ssh_conn,
                    ssh_password=ssh_password,
                )

            # Обновляем UI
            self._on_connected()
            self._load_tree()

            self._status_label.setText(
                f"Подключено к {db_type.value}"
            )
            self._status_label.setStyleSheet(
                "color: #10B981; font-weight: bold; padding-left: 8px;"
            )

        except Exception as e:
            self._status_label.setText(f"Ошибка: {e}")
            self._status_label.setStyleSheet(
                "color: #EF4444; padding-left: 8px;"
            )
            self._connect_btn.setEnabled(True)
            logger.error("Ошибка подключения к БД: %s", e)
            QMessageBox.warning(
                self, "Ошибка подключения",
                f"Не удалось подключиться к базе данных:\n{e}",
            )

    def _disconnect(self) -> None:
        """Отключиться от базы данных."""
        self._db_conn.disconnect()
        self._tree.clear()
        self._on_disconnected()
        self._status_label.setText("Отключено")
        self._status_label.setStyleSheet(
            "color: #71717A; padding-left: 8px;"
        )

    def _on_connected(self) -> None:
        """UI-состояние: подключено."""
        self._connect_btn.setEnabled(False)
        self._disconnect_btn.setEnabled(True)
        self._exec_btn.setEnabled(True)
        self._export_csv_btn.setEnabled(True)
        self._export_json_btn.setEnabled(True)

    def _on_disconnected(self) -> None:
        """UI-состояние: отключено."""
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)
        self._exec_btn.setEnabled(False)
        self._export_csv_btn.setEnabled(False)
        self._export_json_btn.setEnabled(False)

    # --- Дерево ---

    def _load_tree(self) -> None:
        """Загрузить дерево баз данных и таблиц."""
        self._tree.clear()

        databases = self._db_conn.list_databases()
        for db_name in databases:
            db_item = QTreeWidgetItem([f"📦 {db_name}"])
            db_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "database", "name": db_name})

            # Загружаем таблицы
            try:
                tables = self._db_conn.list_tables(db_name)
                for table_name in tables:
                    tbl_item = QTreeWidgetItem([f"📋 {table_name}"])
                    tbl_item.setData(
                        0, Qt.ItemDataRole.UserRole,
                        {"type": "table", "name": table_name, "database": db_name},
                    )
                    db_item.addChild(tbl_item)
            except Exception as e:
                err_item = QTreeWidgetItem([f"⚠ Ошибка: {e}"])
                db_item.addChild(err_item)

            self._tree.addTopLevelItem(db_item)

        self._tree.expandAll()

    def _on_tree_click(self, item: QTreeWidgetItem, column: int) -> None:
        """Клик по элементу дерева."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

    def _on_tree_double_click(self, item: QTreeWidgetItem, column: int) -> None:
        """Двойной клик - открыть таблицу."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        if data.get("type") == "table":
            db_name = data.get("database", "")
            table_name = data["name"]

            # Переключаем БД если нужно
            if db_name and db_name != self._db_conn.current_database:
                try:
                    self._db_conn.switch_database(db_name)
                except Exception as e:
                    QMessageBox.warning(
                        self, "Ошибка",
                        f"Не удалось переключиться на {db_name}:\n{e}",
                    )
                    return

            self._table_view.load_table(table_name)

        elif data.get("type") == "database":
            # Переключаем БД и обновляем дерево
            db_name = data["name"]
            try:
                self._db_conn.switch_database(db_name)
                self._status_label.setText(f"База: {db_name}")
            except Exception as e:
                QMessageBox.warning(
                    self, "Ошибка",
                    f"Не удалось переключиться на {db_name}:\n{e}",
                )

    # --- SQL ---

    def _execute_sql(self) -> None:
        """Выполнить SQL-запрос из редактора."""
        sql = self._sql_edit.toPlainText().strip()
        if not sql:
            return

        if not self._db_conn.is_connected:
            QMessageBox.warning(self, "Ошибка", "Нет подключения к БД")
            return

        self._sql_status.setText("Выполняется...")
        result = self._db_conn.execute_query(sql)

        if result.is_error:
            self._sql_status.setText(f"Ошибка: {result.error}")
            self._sql_status.setStyleSheet(
                "color: #EF4444; padding-left: 8px;"
            )
            self._table_view.load_query_result(result, "SQL")
        elif result.columns:
            # SELECT - показываем результат
            self._sql_status.setText(
                f"Результат: {result.row_count} строк "
                f"({result.execution_time:.3f}с)"
            )
            self._sql_status.setStyleSheet(
                "color: #10B981; padding-left: 8px;"
            )
            self._table_view.load_query_result(result, "SQL")
        else:
            # DML
            self._sql_status.setText(
                f"Затронуто строк: {result.affected_rows} "
                f"({result.execution_time:.3f}с)"
            )
            self._sql_status.setStyleSheet(
                "color: #10B981; padding-left: 8px;"
            )
            self._table_view.load_query_result(result, "SQL")
            # Обновляем дерево (могли создать/удалить таблицу)
            self._load_tree()

    # --- Экспорт ---

    def _export_result(self, fmt: str) -> None:
        """Экспорт текущего результата в файл.

        Args:
            fmt: Формат ("csv" или "json").
        """
        result = self._table_view.get_current_result()
        if not result.rows:
            QMessageBox.information(self, "Экспорт", "Нет данных для экспорта")
            return

        if fmt == "csv":
            ext = "CSV (*.csv)"
            default_name = "export.csv"
        else:
            ext = "JSON (*.json)"
            default_name = "export.json"

        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт результата", default_name, ext,
        )
        if not path:
            return

        try:
            if fmt == "csv":
                content = result.to_csv()
            else:
                content = result.to_json()

            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

            QMessageBox.information(
                self, "Экспорт",
                f"Экспортировано {result.row_count} строк в {path}",
            )
        except Exception as e:
            QMessageBox.warning(
                self, "Ошибка экспорта", str(e),
            )

    # --- Публичные ---

    def refresh_connections(self) -> None:
        """Обновить список подключений (при добавлении нового)."""
        self._refresh_connections()

    def cleanup(self) -> None:
        """Очистка при закрытии."""
        self._db_conn.disconnect()
