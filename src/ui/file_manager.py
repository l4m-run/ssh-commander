# -*- coding: utf-8 -*-
"""Двухпанельный файловый менеджер (Commander-стиль).

Объединяет две FilePanel и кнопки действий для копирования файлов
между панелями (локальная<->сервер, сервер<->сервер).
"""

from __future__ import annotations

import json
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

        # Подключаем Drag & Drop
        self._left_panel.drop_received.connect(
            lambda data, src_id: self._on_drop(data, src_id, self._left_panel)
        )
        self._right_panel.drop_received.connect(
            lambda data, src_id: self._on_drop(data, src_id, self._right_panel)
        )

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

        # Разворачиваем директории в плоский список файлов
        files = [e for e in entries if not e.is_dir]
        dirs = [e for e in entries if e.is_dir]

        # Определяем направление
        if src_conn is None and dst_conn is None:
            # Локальная -> Локальная: обычное копирование
            all_files = self._check_overwrite_local(files, dest_path)
            if all_files is None:
                return
            self._local_copy(all_files, dest_path)
            # Копирование директорий локально
            self._local_copy_dirs(dirs, dest_path)
            dest_panel._refresh()
            return

        if not files and not dirs:
            QMessageBox.information(
                self, "Копирование",
                "Нет файлов для копирования.",
            )
            return

        if src_conn is None and dst_conn is not None:
            direction = TransferDirection.UPLOAD
        elif src_conn is not None and dst_conn is None:
            direction = TransferDirection.DOWNLOAD
        else:
            direction = TransferDirection.SERVER_TO_SERVER

        # Проверяем перезапись для удалённых файлов
        files = self._check_overwrite_remote(
            files, dest_path, dest_panel, direction,
        )
        if not files:
            return

        # Создаём worker
        self._worker = TransferWorker()
        self._worker.progress.connect(self._on_progress)
        self._worker.task_completed.connect(self._on_task_completed)
        self._worker.task_error.connect(self._on_task_error)
        self._worker.all_completed.connect(
            lambda: self._on_all_completed(dest_panel)
        )

        # Добавляем задачи для обычных файлов
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

        # Копирование директорий через SFTP (рекурсивно)
        for d in dirs:
            dir_name = os.path.basename(d.path)
            if direction == TransferDirection.DOWNLOAD:
                dest_dir = os.path.join(dest_path, dir_name)
            else:
                dest_dir = f"{dest_path.rstrip('/')}/{dir_name}"

            dir_files = self._flatten_dir(d, source_panel)
            for rel_path, entry in dir_files:
                if direction == TransferDirection.DOWNLOAD:
                    df = os.path.join(dest_dir, rel_path)
                    os.makedirs(os.path.dirname(df), exist_ok=True)
                else:
                    df = f"{dest_dir}/{rel_path}"
                    # Создание директорий на сервере будет через task

                task = TransferTask(
                    source_path=entry.path,
                    dest_path=df,
                    direction=direction,
                    source_connection=src_conn,
                    dest_connection=dst_conn,
                    total_bytes=entry.size,
                )
                task_id = self._worker.add_task(task)
                self._active_tasks[task_id] = task

        total_tasks = len(self._active_tasks)
        if total_tasks == 0:
            return

        # Показываем прогресс
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._progress_bar.setMaximum(total_tasks)
        self._progress_label.setText(
            f"Передача {total_tasks} файлов..."
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

    def _local_copy_dirs(self, dirs: list[FileEntry], dest_path: str) -> None:
        """Рекурсивно копировать директории локально."""
        import shutil
        for entry in dirs:
            dest = os.path.join(dest_path, os.path.basename(entry.path))
            try:
                shutil.copytree(entry.path, dest, dirs_exist_ok=True)
            except Exception as e:
                QMessageBox.warning(
                    self, "Ошибка",
                    f"Не удалось скопировать {entry.name}:\n{e}",
                )

    def _flatten_dir(
        self, dir_entry: FileEntry, panel: FilePanel,
    ) -> list[tuple[str, FileEntry]]:
        """Рекурсивно развернуть директорию в плоский список (rel_path, entry)."""
        result: list[tuple[str, FileEntry]] = []
        conn = panel.get_current_connection()

        if conn is None:
            # Локальное сканирование
            base = dir_entry.path
            for root, _dirs, fnames in os.walk(base):
                for fname in fnames:
                    full = os.path.join(root, fname)
                    rel = os.path.relpath(full, base)
                    try:
                        st = os.stat(full)
                        result.append((rel, FileEntry(
                            name=fname, path=full,
                            is_dir=False, size=st.st_size,
                        )))
                    except OSError:
                        continue
        else:
            # SFTP сканирование
            sftp = panel._sftp_browser
            self._sftp_walk(sftp, dir_entry.path, "", result)

        return result

    def _sftp_walk(
        self,
        sftp: "SFTPBrowser",
        base_path: str,
        rel_prefix: str,
        result: list[tuple[str, FileEntry]],
    ) -> None:
        """Рекурсивный обход директории по SFTP."""
        entries = sftp.list_dir(base_path)
        for entry in entries:
            rel = f"{rel_prefix}/{entry.name}" if rel_prefix else entry.name
            if entry.is_dir:
                self._sftp_walk(sftp, entry.path, rel, result)
            else:
                result.append((rel, entry))

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

    # --- Проверка перезаписи ---

    def _check_overwrite_local(
        self, files: list[FileEntry], dest_path: str,
    ) -> list[FileEntry]:
        """Проверить существование файлов локально."""
        existing = []
        for f in files:
            dest = os.path.join(dest_path, os.path.basename(f.path))
            if os.path.exists(dest):
                existing.append(f.name)

        if existing:
            return self._ask_overwrite(files, existing)
        return files

    def _check_overwrite_remote(
        self,
        files: list[FileEntry],
        dest_path: str,
        dest_panel: FilePanel,
        direction: TransferDirection,
    ) -> list[FileEntry]:
        """Проверить существование файлов на приёмнике."""
        # Собираем имена файлов в директории назначения
        if direction == TransferDirection.DOWNLOAD:
            # Назначение - локальная
            return self._check_overwrite_local(files, dest_path)

        # Назначение - сервер: смотрим что есть в панели
        dest_names = {e.name for e in dest_panel._entries}
        existing = [
            f.name for f in files
            if os.path.basename(f.path) in dest_names
        ]

        if existing:
            return self._ask_overwrite(files, existing)
        return files

    def _ask_overwrite(
        self, files: list[FileEntry], existing: list[str],
    ) -> list[FileEntry]:
        """Спросить пользователя о перезаписи."""
        names_preview = "\n".join(existing[:10])
        if len(existing) > 10:
            names_preview += f"\n... и ещё {len(existing) - 10}"

        reply = QMessageBox.question(
            self,
            "Перезапись файлов",
            f"Следующие файлы уже существуют:\n\n{names_preview}"
            f"\n\nПерезаписать?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            return files
        return []

    # --- Drag & Drop ---

    def _on_drop(
        self,
        files_data: list[dict],
        source_panel_id: str,
        dest_panel: FilePanel,
    ) -> None:
        """Обработка drop файлов на панель."""
        # Определяем панель-источник
        if str(id(self._left_panel)) == source_panel_id:
            source_panel = self._left_panel
        elif str(id(self._right_panel)) == source_panel_id:
            source_panel = self._right_panel
        else:
            return

        # Восстанавливаем FileEntry из сериализованных данных
        entries = [
            FileEntry(
                name=d["name"],
                path=d["path"],
                is_dir=d["is_dir"],
                size=d["size"],
            )
            for d in files_data
        ]

        self._start_transfer(
            entries,
            source_panel=source_panel,
            dest_panel=dest_panel,
        )
