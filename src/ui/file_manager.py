# -*- coding: utf-8 -*-
"""Двухпанельный файловый менеджер (Commander-стиль).

Объединяет две FilePanel и кнопки действий для копирования файлов
между панелями (локальная<->сервер, сервер<->сервер).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.core.sftp_worker import (
    FileEntry,
    TransferDirection,
    TransferTask,
    TransferWorker,
)
from src.ui.file_panel import FilePanel

if TYPE_CHECKING:
    from src.core.database import Database


class FileManager(QWidget):
    """Двухпанельный файловый менеджер.

    Layout:
    ┌─────────────────┬─────────────────┐
    │  Левая панель   │  Правая панель  │
    │  (локальная/    │  (локальная/    │
    │   сервер)       │   сервер)       │
    ├─────────────────┴─────────────────┤
    │  [◄ Копировать] [Копировать ►]    │
    ├───────────────────────────────────┤
    │  Прогресс: ████████░░░ 65%        │
    └───────────────────────────────────┘
    """

    def __init__(
        self,
        db: Database,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._worker: TransferWorker | None = None
        self._active_tasks: dict[int, TransferTask] = {}

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Создание UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Сплиттер с двумя панелями
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._left_panel = FilePanel(self._db, label="Левая")
        self._right_panel = FilePanel(self._db, label="Правая")

        splitter.addWidget(self._left_panel)
        splitter.addWidget(self._right_panel)
        splitter.setSizes([500, 500])

        layout.addWidget(splitter, stretch=1)

        # Кнопки действий
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)

        self._copy_left_btn = QPushButton("◄ Копировать сюда")
        self._copy_left_btn.setToolTip("Копировать выделенные файлы из правой панели в левую")
        self._copy_left_btn.clicked.connect(self._copy_right_to_left)
        actions_row.addWidget(self._copy_left_btn)

        self._copy_right_btn = QPushButton("Копировать сюда ►")
        self._copy_right_btn.setToolTip("Копировать выделенные файлы из левой панели в правую")
        self._copy_right_btn.clicked.connect(self._copy_left_to_right)
        actions_row.addWidget(self._copy_right_btn)

        layout.addLayout(actions_row)

        # Прогресс-бар
        progress_row = QHBoxLayout()
        self._progress_label = QLabel("")
        progress_row.addWidget(self._progress_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setTextVisible(True)
        progress_row.addWidget(self._progress_bar)

        layout.addLayout(progress_row)

    def _copy_left_to_right(self) -> None:
        """Копировать из левой панели в правую."""
        entries = self._left_panel.get_selected_entries()
        if not entries:
            QMessageBox.information(
                self, "Копирование",
                "Выберите файлы в левой панели.",
            )
            return
        self._start_transfer(
            entries,
            source_panel=self._left_panel,
            dest_panel=self._right_panel,
        )

    def _copy_right_to_left(self) -> None:
        """Копировать из правой панели в левую."""
        entries = self._right_panel.get_selected_entries()
        if not entries:
            QMessageBox.information(
                self, "Копирование",
                "Выберите файлы в правой панели.",
            )
            return
        self._start_transfer(
            entries,
            source_panel=self._right_panel,
            dest_panel=self._left_panel,
        )

    def _start_transfer(
        self,
        entries: list[FileEntry],
        source_panel: FilePanel,
        dest_panel: FilePanel,
    ) -> None:
        """Запустить передачу файлов.

        Args:
            entries: Список файлов для передачи.
            source_panel: Панель-источник.
            dest_panel: Панель назначения.
        """
        # Определяем направление
        src_conn = source_panel.get_current_connection()
        dst_conn = dest_panel.get_current_connection()
        dest_path = dest_panel.get_current_path()

        # Пропускаем директории (пока не поддерживаем рекурсивное копирование)
        files = [e for e in entries if not e.is_dir]
        if not files:
            QMessageBox.information(
                self, "Копирование",
                "Выберите файлы (копирование директорий пока не поддерживается).",
            )
            return

        # Определяем направление
        if src_conn is None and dst_conn is None:
            # Локальная -> Локальная: обычное копирование
            self._local_copy(files, dest_path)
            dest_panel._refresh()
            return

        if src_conn is None and dst_conn is not None:
            direction = TransferDirection.UPLOAD
        elif src_conn is not None and dst_conn is None:
            direction = TransferDirection.DOWNLOAD
        else:
            direction = TransferDirection.SERVER_TO_SERVER

        # Создаём worker
        self._worker = TransferWorker()
        self._worker.progress.connect(self._on_progress)
        self._worker.task_completed.connect(self._on_task_completed)
        self._worker.task_error.connect(self._on_task_error)
        self._worker.all_completed.connect(
            lambda: self._on_all_completed(dest_panel)
        )

        # Добавляем задачи
        for entry in files:
            filename = os.path.basename(entry.path)
            if direction == TransferDirection.DOWNLOAD:
                dest_file = os.path.join(dest_path, filename)
            else:
                dest_file = f"{dest_path.rstrip('/')}/{filename}"

            task = TransferTask(
                source_path=entry.path,
                dest_path=dest_file,
                direction=direction,
                source_connection=src_conn,
                dest_connection=dst_conn,
                total_bytes=entry.size,
            )
            task_id = self._worker.add_task(task)
            self._active_tasks[task_id] = task

        # Показываем прогресс
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._progress_bar.setMaximum(len(files))
        self._progress_label.setText(
            f"Передача {len(files)} файлов..."
        )

        # Запуск
        self._worker.start()

    def _local_copy(self, files: list[FileEntry], dest_path: str) -> None:
        """Копировать файлы локально."""
        import shutil
        for entry in files:
            dest = os.path.join(dest_path, os.path.basename(entry.path))
            try:
                shutil.copy2(entry.path, dest)
            except Exception as e:
                QMessageBox.warning(
                    self, "Ошибка",
                    f"Не удалось скопировать {entry.name}:\n{e}",
                )

    def _on_progress(
        self, task_id: int, transferred: int, total: int
    ) -> None:
        """Обновление прогресса."""
        if total > 0:
            pct = int(transferred / total * 100)
            task = self._active_tasks.get(task_id)
            name = os.path.basename(task.source_path) if task else ""
            self._progress_label.setText(
                f"Передача: {name} ({pct}%)"
            )

    def _on_task_completed(self, task_id: int) -> None:
        """Задача завершена."""
        self._active_tasks.pop(task_id, None)
        completed = self._progress_bar.maximum() - len(self._active_tasks)
        self._progress_bar.setValue(completed)

    def _on_task_error(self, task_id: int, error: str) -> None:
        """Ошибка задачи."""
        task = self._active_tasks.pop(task_id, None)
        name = os.path.basename(task.source_path) if task else "?"
        QMessageBox.warning(
            self, "Ошибка передачи",
            f"Файл: {name}\nОшибка: {error}",
        )

    def _on_all_completed(self, dest_panel: FilePanel) -> None:
        """Все задачи завершены."""
        self._progress_bar.setVisible(False)
        self._progress_label.setText("Передача завершена")
        self._active_tasks.clear()
        dest_panel._refresh()

    def refresh_connections(self) -> None:
        """Обновить списки подключений в обеих панелях."""
        self._left_panel.refresh_connections()
        self._right_panel.refresh_connections()

    def cleanup(self) -> None:
        """Очистка при закрытии."""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)
        self._left_panel.cleanup()
        self._right_panel.cleanup()
