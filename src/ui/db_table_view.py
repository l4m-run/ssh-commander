# -*- coding: utf-8 -*-
"""Виджет таблицы данных из БД.

Отображает строки таблицы с пагинацией, сортировкой
и inline-редактированием ячеек.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.db_manager import ColumnInfo, DatabaseConnection, QueryResult

logger = logging.getLogger(__name__)

# Размер страницы по умолчанию
PAGE_SIZE = 100


class DbTableView(QWidget):
    """Виджет отображения и редактирования данных таблицы.

    Layout:
    ┌─────────────────────────────────────────┐
    │  Таблица: users  (10 колонок, 150 строк)│
    ├─────────────────────────────────────────┤
    │  │ id │ name  │ email           │ ...   │
    │  ├────┼───────┼─────────────────┼───    │
    │  │ 1  │ Admin │ admin@test.com  │       │
    │  │ 2  │ User  │ user@test.com   │       │
    ├─────────────────────────────────────────┤
    │  [◄] Страница 1/3  [►]  100 строк/стр  │
    └─────────────────────────────────────────┘

    Signals:
        cell_updated: Ячейка обновлена (table, column, row_pk, new_value).
    """

    cell_updated = Signal(str, str, dict, object)

    def __init__(
        self,
        db: DatabaseConnection,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._table_name: str = ""
        self._columns: list[ColumnInfo] = []
        self._pk_columns: list[str] = []
        self._total_rows: int = 0
        self._current_page: int = 0
        self._order_by: str = ""
        self._order_desc: bool = False
        self._editing = False

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Создание UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Заголовок
        self._header_label = QLabel("Выберите таблицу")
        self._header_label.setStyleSheet(
            "font-weight: bold; font-size: 13px; padding: 4px;"
        )
        layout.addWidget(self._header_label)

        # Таблица данных
        self._table = QTableWidget()
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)  # Сортируем через SQL
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectItems
        )
        self._table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )

        # Клик по заголовку - сортировка
        self._table.horizontalHeader().sectionClicked.connect(
            self._on_header_clicked
        )
        # Изменение ячейки
        self._table.cellChanged.connect(self._on_cell_changed)

        layout.addWidget(self._table, stretch=1)

        # Пагинация
        page_row = QHBoxLayout()
        page_row.setSpacing(8)

        btn_style = (
            "QPushButton { background: #E5E5E7; color: #18181B;"
            " border: 1px solid #D4D4D8; border-radius: 4px;"
            " padding: 4px 12px; font-size: 12px; }"
            "QPushButton:hover { background: #D4D4D8; }"
            "QPushButton:disabled { color: #A1A1AA; }"
        )

        self._prev_btn = QPushButton("◄ Назад")
        self._prev_btn.setStyleSheet(btn_style)
        self._prev_btn.clicked.connect(self._prev_page)
        page_row.addWidget(self._prev_btn)

        self._page_label = QLabel("")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_row.addWidget(self._page_label, stretch=1)

        self._next_btn = QPushButton("Вперёд ►")
        self._next_btn.setStyleSheet(btn_style)
        self._next_btn.clicked.connect(self._next_page)
        page_row.addWidget(self._next_btn)

        layout.addLayout(page_row)

    def load_table(self, table_name: str) -> None:
        """Загрузить данные таблицы.

        Args:
            table_name: Имя таблицы.
        """
        self._table_name = table_name
        self._columns = self._db.get_columns(table_name)
        self._pk_columns = self._db.get_primary_keys(table_name)
        self._total_rows = self._db.count_rows(table_name)
        self._current_page = 0
        self._order_by = ""
        self._order_desc = False

        self._reload_data()

    def load_query_result(self, result: QueryResult, title: str = "") -> None:
        """Показать результат произвольного SQL-запроса.

        Args:
            result: Результат запроса.
            title: Заголовок.
        """
        self._table_name = ""
        self._columns = []
        self._pk_columns = []

        if result.is_error:
            self._header_label.setText(f"Ошибка: {result.error}")
            self._table.clear()
            self._table.setRowCount(0)
            self._table.setColumnCount(0)
            self._update_pagination(0)
            return

        if not result.columns:
            # DML запрос без результата
            info = title or "Запрос выполнен"
            self._header_label.setText(
                f"{info} | Затронуто строк: {result.affected_rows} "
                f"| Время: {result.execution_time:.3f}с"
            )
            self._table.clear()
            self._table.setRowCount(0)
            self._table.setColumnCount(0)
            self._update_pagination(0)
            return

        # SELECT - показываем результат
        self._header_label.setText(
            f"{title or 'Результат'} | {result.row_count} строк "
            f"| Время: {result.execution_time:.3f}с"
        )

        self._display_rows(result.columns, result.rows, editable=False)
        self._total_rows = result.row_count
        self._update_pagination(result.row_count)

    def get_current_result(self) -> QueryResult:
        """Получить текущие данные таблицы как QueryResult (для экспорта)."""
        columns = []
        for col in range(self._table.columnCount()):
            item = self._table.horizontalHeaderItem(col)
            columns.append(item.text() if item else f"col_{col}")

        rows = []
        for row in range(self._table.rowCount()):
            row_data = []
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                row_data.append(item.text() if item else "")
            rows.append(tuple(row_data))

        return QueryResult(columns=columns, rows=rows)

    def _reload_data(self) -> None:
        """Перезагрузить данные текущей таблицы."""
        if not self._table_name:
            return

        offset = self._current_page * PAGE_SIZE
        result = self._db.fetch_rows(
            self._table_name,
            limit=PAGE_SIZE,
            offset=offset,
            order_by=self._order_by,
            order_desc=self._order_desc,
        )

        if result.is_error:
            QMessageBox.warning(self, "Ошибка", result.error)
            return

        # Заголовок
        col_info = ""
        if self._columns:
            col_info = f"{len(self._columns)} колонок, "
        sort_info = ""
        if self._order_by:
            direction = "▼" if self._order_desc else "▲"
            sort_info = f" | Сортировка: {self._order_by} {direction}"

        self._header_label.setText(
            f"Таблица: {self._table_name} | "
            f"{col_info}{self._total_rows} строк{sort_info}"
        )

        # Заголовки колонок с типами
        headers = []
        for col in self._columns:
            pk_mark = "🔑 " if col.is_pk else ""
            headers.append(f"{pk_mark}{col.name}\n({col.type})")

        display_columns = headers if headers else result.columns

        self._display_rows(
            display_columns, result.rows,
            editable=bool(self._pk_columns),
        )
        self._update_pagination(self._total_rows)

    def _display_rows(
        self,
        columns: list[str],
        rows: list[tuple],
        editable: bool = False,
    ) -> None:
        """Отобразить строки в таблице.

        Args:
            columns: Заголовки колонок.
            rows: Данные строк.
            editable: Разрешить редактирование.
        """
        self._editing = True  # Блокируем обработку cellChanged

        self._table.clear()
        self._table.setColumnCount(len(columns))
        self._table.setRowCount(len(rows))
        self._table.setHorizontalHeaderLabels(columns)

        for row_idx, row in enumerate(rows):
            for col_idx, value in enumerate(row):
                text = "" if value is None else str(value)
                item = QTableWidgetItem(text)

                if not editable:
                    item.setFlags(
                        item.flags() & ~Qt.ItemFlag.ItemIsEditable
                    )

                # NULL значения серым
                if value is None:
                    item.setText("NULL")
                    item.setForeground(Qt.GlobalColor.gray)

                self._table.setItem(row_idx, col_idx, item)

        # Автоширина колонок
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.resizeColumnsToContents()

        # Растянуть последнюю колонку
        if self._table.columnCount() > 0:
            header.setStretchLastSection(True)

        self._editing = False

    def _update_pagination(self, total: int) -> None:
        """Обновить элементы пагинации."""
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        current = self._current_page + 1

        self._page_label.setText(
            f"Страница {current}/{total_pages} "
            f"({PAGE_SIZE} строк/стр, всего {total})"
        )
        self._prev_btn.setEnabled(self._current_page > 0)
        self._next_btn.setEnabled(current < total_pages)

    def _prev_page(self) -> None:
        """Предыдущая страница."""
        if self._current_page > 0:
            self._current_page -= 1
            self._reload_data()

    def _next_page(self) -> None:
        """Следующая страница."""
        total_pages = max(1, (self._total_rows + PAGE_SIZE - 1) // PAGE_SIZE)
        if self._current_page + 1 < total_pages:
            self._current_page += 1
            self._reload_data()

    def _on_header_clicked(self, logical_index: int) -> None:
        """Клик по заголовку - сортировка."""
        if not self._table_name or not self._columns:
            return

        col_name = self._columns[logical_index].name

        if self._order_by == col_name:
            # Переключаем направление
            self._order_desc = not self._order_desc
        else:
            self._order_by = col_name
            self._order_desc = False

        self._current_page = 0
        self._reload_data()

    def _on_cell_changed(self, row: int, col: int) -> None:
        """Обработка изменения ячейки (inline-редактирование)."""
        if self._editing:
            return
        if not self._table_name or not self._pk_columns:
            return

        item = self._table.item(row, col)
        if not item:
            return

        new_value = item.text()
        if new_value == "NULL":
            new_value = None

        # Определяем колонку
        if col >= len(self._columns):
            return
        col_info = self._columns[col]

        # Собираем значения PK из текущей строки
        pk_values = []
        for pk_col in self._pk_columns:
            pk_idx = next(
                (i for i, c in enumerate(self._columns) if c.name == pk_col),
                None,
            )
            if pk_idx is None:
                return
            pk_item = self._table.item(row, pk_idx)
            pk_values.append(pk_item.text() if pk_item else None)

        # Подтверждение
        pk_info = ", ".join(
            f"{k}={v}" for k, v in zip(self._pk_columns, pk_values)
        )
        reply = QMessageBox.question(
            self,
            "Подтверждение изменения",
            f"Изменить {col_info.name} = '{new_value}'\n"
            f"в таблице {self._table_name}\n"
            f"WHERE {pk_info}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            # Откатываем
            self._reload_data()
            return

        # Выполняем UPDATE
        result = self._db.update_cell(
            self._table_name,
            self._pk_columns,
            pk_values,
            col_info.name,
            new_value,
        )

        if result.is_error:
            QMessageBox.warning(
                self, "Ошибка обновления", result.error,
            )
            self._reload_data()
        else:
            logger.info(
                "Обновлено: %s.%s WHERE %s",
                self._table_name, col_info.name, pk_info,
            )
