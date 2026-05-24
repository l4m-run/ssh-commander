# -*- coding: utf-8 -*-
"""Панель файлового браузера.

Одна сторона двухпанельного менеджера.
Может быть привязана к локальной ФС или к удалённому серверу через SFTP.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path
from typing import TYPE_CHECKING

import json

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.crypto import crypto
from src.core.sftp_worker import FileEntry, SFTPBrowser

if TYPE_CHECKING:
    from src.core.database import Database
    from src.models.connection import Connection


def _format_size(size: int) -> str:
    """Форматировать размер файла в человекочитаемый вид."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"


def _format_time(ts: float) -> str:
    """Форматировать timestamp в строку."""
    if ts == 0:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


class FilePanel(QWidget):
    """Панель файлового браузера (одна сторона).

    Может отображать локальную ФС или удалённую (SFTP).

    Signals:
        file_selected: Выбран файл (FileEntry).
        path_changed: Изменился текущий путь (str).
        transfer_requested: Запрос на копирование файлов (list[FileEntry], str - dest_path).
    """

    file_selected = Signal(object)
    path_changed = Signal(str)
    transfer_requested = Signal(list, str)
    # Сигнал: файлы были сброшены на эту панель (list[dict], source_panel_id)
    drop_received = Signal(list, str)

    def __init__(
        self,
        db: Database,
        label: str = "Панель",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._label = label

        # Режим: "local" или "remote"
        self._mode = "local"
        self._local_path = str(Path.home())
        self._sftp_browser = SFTPBrowser()
        self._current_connection: Connection | None = None
        self._entries: list[FileEntry] = []

        self._setup_ui()
        self._refresh()

    def _setup_ui(self) -> None:
        """Создание UI панели."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Строка выбора источника
        source_row = QHBoxLayout()
        self._source_combo = QComboBox()
        self._source_combo.addItem("Локальная", "local")
        self._update_connections_list()
        self._source_combo.currentIndexChanged.connect(self._on_source_changed)
        source_row.addWidget(self._source_combo)
        layout.addLayout(source_row)

        # Строка пути
        path_row = QHBoxLayout()
        path_row.setSpacing(2)

        nav_btn_style = (
            "QPushButton { background: #E5E5E7; color: #18181B;"
            " border: 1px solid #D4D4D8; border-radius: 4px;"
            " padding: 2px 8px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background: #D4D4D8; }"
        )

        self._up_btn = QPushButton("Up")
        self._up_btn.setFixedHeight(28)
        self._up_btn.setToolTip("Вверх (родительская директория)")
        self._up_btn.setStyleSheet(nav_btn_style)
        self._up_btn.clicked.connect(self._go_up)
        path_row.addWidget(self._up_btn)

        self._home_btn = QPushButton("Home")
        self._home_btn.setFixedHeight(28)
        self._home_btn.setToolTip("Домашняя директория")
        self._home_btn.setStyleSheet(nav_btn_style)
        self._home_btn.clicked.connect(self._go_home)
        path_row.addWidget(self._home_btn)

        self._path_edit = QLineEdit()
        self._path_edit.returnPressed.connect(self._on_path_entered)
        path_row.addWidget(self._path_edit)

        self._refresh_btn = QPushButton("Обновить")
        self._refresh_btn.setFixedHeight(28)
        self._refresh_btn.setToolTip("Обновить список файлов")
        self._refresh_btn.setStyleSheet(nav_btn_style)
        self._refresh_btn.clicked.connect(self._refresh)
        path_row.addWidget(self._refresh_btn)

        layout.addLayout(path_row)

        # Строка поиска/фильтрации
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Фильтр по имени...")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter_edit)

        # Таблица файлов
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Имя", "Размер", "Изменён", "Права"])
        self._tree.setRootIsDecorated(False)
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self._tree.setSortingEnabled(True)
        self._tree.itemDoubleClicked.connect(self._on_item_double_click)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)

        # Drag & Drop
        self._tree.setDragEnabled(True)
        self._tree.setAcceptDrops(True)
        self._tree.setDragDropMode(QTreeWidget.DragDropMode.DragDrop)
        self._tree.setDefaultDropAction(Qt.DropAction.CopyAction)
        # Перехватываем события drag/drop вручную
        self._tree.startDrag = self._start_drag
        self._tree.dragEnterEvent = self._drag_enter
        self._tree.dragMoveEvent = self._drag_move
        self._tree.dropEvent = self._drop_event

        # Настройка колонок
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        layout.addWidget(self._tree)

        # Статус
        self._status_label = QLabel()
        self._status_label.setProperty("secondary", True)
        layout.addWidget(self._status_label)

    def _update_connections_list(self) -> None:
        """Обновить список подключений в комбобоксе."""
        # Сохраняем текущий выбор
        current_data = self._source_combo.currentData()
        # Удаляем всё кроме "Локальная"
        while self._source_combo.count() > 1:
            self._source_combo.removeItem(1)
        # Добавляем подключения
        for conn in self._db.get_all_connections():
            self._source_combo.addItem(
                f"🖥 {conn.display_name}", f"remote:{conn.id}"
            )
        # Восстанавливаем выбор
        if current_data:
            idx = self._source_combo.findData(current_data)
            if idx >= 0:
                self._source_combo.setCurrentIndex(idx)

    def _on_source_changed(self, index: int) -> None:
        """Обработка смены источника (локальная/сервер)."""
        data = self._source_combo.currentData()
        if data == "local":
            self._disconnect_sftp()
            self._mode = "local"
            self._refresh()
        elif data and data.startswith("remote:"):
            conn_id = int(data.split(":")[1])
            self._connect_to_server(conn_id)

    def _connect_to_server(self, conn_id: int) -> None:
        """Подключиться к серверу по SFTP."""
        conn = self._db.get_connection(conn_id)
        if not conn:
            return

        password = ""
        if conn.encrypted_password:
            try:
                password = crypto.decrypt(conn.encrypted_password)
            except Exception as e:
                QMessageBox.warning(
                    self, "Ошибка",
                    f"Не удалось расшифровать пароль:\n{e}",
                )
                return

        try:
            self._sftp_browser.connect(conn, password)
            self._mode = "remote"
            self._current_connection = conn
            self._refresh()
        except Exception as e:
            QMessageBox.warning(
                self, "Ошибка подключения",
                f"Не удалось подключиться по SFTP:\n{e}",
            )
            self._source_combo.setCurrentIndex(0)

    def _disconnect_sftp(self) -> None:
        """Отключить SFTP."""
        self._sftp_browser.disconnect()
        self._current_connection = None

    def _refresh(self) -> None:
        """Обновить список файлов."""
        self._tree.clear()
        self._entries.clear()
        self._filter_edit.clear()

        if self._mode == "local":
            self._load_local_files()
        else:
            self._load_remote_files()

        self._populate_tree()

    def _load_local_files(self) -> None:
        """Загрузить список локальных файлов."""
        self._path_edit.setText(self._local_path)
        try:
            for name in os.listdir(self._local_path):
                full_path = os.path.join(self._local_path, name)
                try:
                    st = os.stat(full_path)
                    self._entries.append(FileEntry(
                        name=name,
                        path=full_path,
                        is_dir=os.path.isdir(full_path),
                        size=st.st_size,
                        modified=st.st_mtime,
                        permissions=stat.filemode(st.st_mode),
                    ))
                except OSError:
                    continue
        except PermissionError:
            self._status_label.setText("Нет доступа")
            return

        self._entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        self._status_label.setText(
            f"{len(self._entries)} элементов"
        )

    def _load_remote_files(self) -> None:
        """Загрузить список файлов с сервера."""
        if not self._sftp_browser.is_connected:
            return
        self._path_edit.setText(self._sftp_browser.current_path)
        self._entries = self._sftp_browser.list_dir()
        host = ""
        if self._current_connection:
            host = f" @ {self._current_connection.host}"
        self._status_label.setText(
            f"{len(self._entries)} элементов{host}"
        )

    def _populate_tree(self) -> None:
        """Заполнить дерево файлами."""
        for entry in self._entries:
            item = QTreeWidgetItem()
            icon = "📁" if entry.is_dir else "📄"
            item.setText(0, f"{icon} {entry.name}")
            item.setText(1, _format_size(entry.size) if not entry.is_dir else "")
            item.setText(2, _format_time(entry.modified))
            item.setText(3, entry.permissions)
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            self._tree.addTopLevelItem(item)

    def _apply_filter(self, text: str) -> None:
        """Фильтрация файлов по имени."""
        text = text.strip().lower()
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            entry: FileEntry = item.data(0, Qt.ItemDataRole.UserRole)
            if not text or text in entry.name.lower():
                item.setHidden(False)
            else:
                item.setHidden(True)

    def _on_item_double_click(self, item: QTreeWidgetItem, column: int) -> None:
        """Двойной клик - вход в директорию."""
        entry: FileEntry = item.data(0, Qt.ItemDataRole.UserRole)
        if not entry or not entry.is_dir:
            return

        if self._mode == "local":
            self._local_path = entry.path
        else:
            self._sftp_browser.change_dir(entry.path)

        self._refresh()

    def _go_up(self) -> None:
        """Перейти в родительскую директорию."""
        if self._mode == "local":
            parent = str(Path(self._local_path).parent)
            if parent != self._local_path:
                self._local_path = parent
        else:
            self._sftp_browser.go_up()
        self._refresh()

    def _go_home(self) -> None:
        """Перейти в домашнюю директорию."""
        if self._mode == "local":
            self._local_path = str(Path.home())
        else:
            self._sftp_browser.change_dir("~")
        self._refresh()

    def _on_path_entered(self) -> None:
        """Ручной ввод пути."""
        path = self._path_edit.text().strip()
        if not path:
            return
        if self._mode == "local":
            if os.path.isdir(path):
                self._local_path = path
        else:
            self._sftp_browser.change_dir(path)
        self._refresh()

    def _show_context_menu(self, pos) -> None:
        """Контекстное меню."""
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        refresh_action = menu.addAction("Обновить")
        refresh_action.triggered.connect(self._refresh)
        mkdir_action = menu.addAction("Создать папку")
        mkdir_action.triggered.connect(self._create_dir)

        selected = self._tree.selectedItems()
        if selected:
            menu.addSeparator()
            delete_action = menu.addAction("Удалить")
            delete_action.triggered.connect(self._delete_selected)

        menu.exec(self._tree.mapToGlobal(pos))

    def _create_dir(self) -> None:
        """Создать новую директорию."""
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(
            self, "Новая папка", "Имя директории:"
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        if self._mode == "local":
            try:
                os.makedirs(
                    os.path.join(self._local_path, name), exist_ok=True
                )
            except OSError as e:
                QMessageBox.warning(self, "Ошибка", str(e))
                return
        else:
            if not self._sftp_browser.mkdir(name):
                QMessageBox.warning(
                    self, "Ошибка", "Не удалось создать директорию."
                )
                return
        self._refresh()

    def _delete_selected(self) -> None:
        """Удалить выбранные файлы."""
        selected = self._tree.selectedItems()
        if not selected:
            return

        names = [item.data(0, Qt.ItemDataRole.UserRole).name for item in selected]
        reply = QMessageBox.question(
            self, "Удаление",
            f"Удалить {len(names)} элементов?\n" + "\n".join(names[:5]),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for item in selected:
            entry: FileEntry = item.data(0, Qt.ItemDataRole.UserRole)
            if self._mode == "local":
                try:
                    if entry.is_dir:
                        os.rmdir(entry.path)
                    else:
                        os.remove(entry.path)
                except OSError as e:
                    QMessageBox.warning(self, "Ошибка", f"{entry.name}: {e}")
            else:
                self._sftp_browser.delete(entry.path, entry.is_dir)

        self._refresh()

    def get_selected_entries(self) -> list[FileEntry]:
        """Получить выбранные файлы."""
        entries = []
        for item in self._tree.selectedItems():
            entry = item.data(0, Qt.ItemDataRole.UserRole)
            if entry:
                entries.append(entry)
        return entries

    def get_current_path(self) -> str:
        """Получить текущий путь."""
        if self._mode == "local":
            return self._local_path
        return self._sftp_browser.current_path

    def get_current_connection(self) -> Connection | None:
        """Получить текущее подключение (None для локальной)."""
        if self._mode == "remote":
            return self._current_connection
        return None

    def is_local(self) -> bool:
        """Локальная ли панель."""
        return self._mode == "local"

    def refresh_connections(self) -> None:
        """Обновить список подключений (при добавлении нового)."""
        self._update_connections_list()

    def cleanup(self) -> None:
        """Очистка при закрытии."""
        self._disconnect_sftp()

    # --- Drag & Drop ---

    def _start_drag(self, supported_actions) -> None:
        """Начало перетаскивания файлов."""
        entries = self.get_selected_entries()
        if not entries:
            return

        # Сериализуем данные файлов в MIME
        data = []
        for e in entries:
            data.append({
                "name": e.name,
                "path": e.path,
                "is_dir": e.is_dir,
                "size": e.size,
            })

        mime = QMimeData()
        mime.setData(
            "application/x-ssh-commander-files",
            json.dumps(data).encode("utf-8"),
        )
        # Сохраняем ID панели-источника
        mime.setData(
            "application/x-ssh-commander-source",
            str(id(self)).encode("utf-8"),
        )

        drag = QDrag(self._tree)
        drag.setMimeData(mime)
        names = ", ".join(e.name for e in entries[:3])
        if len(entries) > 3:
            names += f" ... (+{len(entries) - 3})"
        drag.exec(Qt.DropAction.CopyAction)

    def _drag_enter(self, event) -> None:
        """Принять drag с нашим MIME-типом."""
        if event.mimeData().hasFormat("application/x-ssh-commander-files"):
            # Не принимаем drop на себя же
            source_id = event.mimeData().data(
                "application/x-ssh-commander-source"
            ).data().decode("utf-8")
            if source_id != str(id(self)):
                event.acceptProposedAction()
                return
        event.ignore()

    def _drag_move(self, event) -> None:
        """Продолжение drag."""
        if event.mimeData().hasFormat("application/x-ssh-commander-files"):
            source_id = event.mimeData().data(
                "application/x-ssh-commander-source"
            ).data().decode("utf-8")
            if source_id != str(id(self)):
                event.acceptProposedAction()
                return
        event.ignore()

    def _drop_event(self, event) -> None:
        """Обработка drop - отправляем сигнал."""
        mime = event.mimeData()
        if not mime.hasFormat("application/x-ssh-commander-files"):
            event.ignore()
            return

        raw = mime.data("application/x-ssh-commander-files").data()
        source_id = mime.data(
            "application/x-ssh-commander-source"
        ).data().decode("utf-8")

        files_data = json.loads(raw.decode("utf-8"))
        self.drop_received.emit(files_data, source_id)
        event.acceptProposedAction()
