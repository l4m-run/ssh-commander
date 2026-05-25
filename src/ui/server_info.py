# -*- coding: utf-8 -*-
"""Системная информация: snapshot состояния удалённого сервера.

Собирает OS, CPU, RAM, диски, сеть, uptime одной SSH-командой.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.core.crypto import crypto
from src.models.connection import Connection

if TYPE_CHECKING:
    from src.core.database import Database

logger = logging.getLogger(__name__)

# Команда для сбора всей информации за один SSH-вызов
_INFO_COMMAND = (
    'echo "===OS===" && cat /etc/os-release 2>/dev/null && '
    'echo "===HOSTNAME===" && hostname -f 2>/dev/null || hostname && '
    'echo "===KERNEL===" && uname -r && '
    'echo "===CPU===" && lscpu 2>/dev/null && '
    'echo "===MEM===" && free -m && '
    'echo "===DISK===" && df -h -x tmpfs -x devtmpfs -x overlay 2>/dev/null && '
    'echo "===UPTIME===" && uptime && '
    'echo "===IP===" && (ip -br addr 2>/dev/null || ifconfig 2>/dev/null)'
)


class _InfoSignals(QObject):
    """Сигналы для воркера."""

    output = Signal(str)
    error = Signal(str)


class _InfoWorker(QRunnable):
    """Воркер для получения системной информации через SSH."""

    def __init__(self, conn: Connection) -> None:
        super().__init__()
        self.signals = _InfoSignals()
        self._conn = conn
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        """Выполнить сбор информации."""
        import paramiko

        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            kwargs: dict = {
                "hostname": self._conn.host,
                "port": self._conn.port,
                "username": self._conn.username,
                "timeout": 15,
            }
            if self._conn.encrypted_password:
                try:
                    kwargs["password"] = crypto.decrypt(
                        self._conn.encrypted_password
                    )
                except Exception:
                    pass
            if self._conn.ssh_key_path:
                kwargs["key_filename"] = self._conn.ssh_key_path

            client.connect(**kwargs)
            _, stdout, stderr = client.exec_command(
                _INFO_COMMAND, timeout=30,
            )
            out = stdout.read().decode("utf-8", errors="replace")
            client.close()

            try:
                self.signals.output.emit(out)
            except RuntimeError:
                pass
        except Exception as e:
            try:
                self.signals.error.emit(str(e))
            except RuntimeError:
                pass


class ServerInfoWidget(QWidget):
    """Виджет системной информации о сервере.

    Отображает snapshot: OS, CPU, RAM, диски, IP, uptime.
    """

    def __init__(
        self,
        app_db: Database,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_db = app_db
        self._thread_pool = QThreadPool()
        self._thread_pool.setMaxThreadCount(2)

        self._setup_ui()
        self._refresh_connections()

    def _setup_ui(self) -> None:
        """Создание UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Выбор сервера
        server_row = QHBoxLayout()
        server_row.addWidget(QLabel("Сервер:"))

        self._server_combo = QComboBox()
        self._server_combo.setMinimumWidth(300)
        server_row.addWidget(self._server_combo, stretch=1)

        self._fetch_btn = QPushButton("Получить информацию")
        self._fetch_btn.setStyleSheet(
            "QPushButton { background: #3B82F6; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #2563EB; }"
        )
        self._fetch_btn.clicked.connect(self._fetch_info)
        server_row.addWidget(self._fetch_btn)

        layout.addLayout(server_row)

        # Область с информацией (скроллируемая)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: none; }"
        )

        self._info_container = QWidget()
        self._info_layout = QVBoxLayout(self._info_container)
        self._info_layout.setSpacing(10)
        self._info_layout.setContentsMargins(4, 4, 4, 4)

        # Начальная подсказка
        self._placeholder = QLabel(
            "Выберите сервер и нажмите \"Получить информацию\""
        )
        self._placeholder.setStyleSheet(
            "color: #9CA3AF; font-size: 14px; padding: 40px;"
        )
        self._info_layout.addWidget(self._placeholder)
        self._info_layout.addStretch()

        scroll.setWidget(self._info_container)
        layout.addWidget(scroll, stretch=1)

    def _refresh_connections(self) -> None:
        """Заполнить комбобокс серверов."""
        self._server_combo.clear()
        self._server_combo.addItem("-- Выберите сервер --", None)
        connections = self._app_db.get_all_connections()
        for conn in connections:
            label = f"{conn.name} ({conn.username}@{conn.host}:{conn.port})"
            self._server_combo.addItem(label, conn.id)

    def _fetch_info(self) -> None:
        """Запросить информацию с сервера."""
        conn_id = self._server_combo.currentData()
        if conn_id is None:
            return

        conn = self._app_db.get_connection(conn_id)
        if not conn:
            return

        self._fetch_btn.setEnabled(False)
        self._fetch_btn.setText("Загрузка...")
        self._clear_info()

        worker = _InfoWorker(conn)
        worker.signals.output.connect(self._on_info_received)
        worker.signals.error.connect(self._on_error)
        self._thread_pool.start(worker)

    def _clear_info(self) -> None:
        """Очистить карточку."""
        while self._info_layout.count():
            item = self._info_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_error(self, error: str) -> None:
        """Обработка ошибки."""
        self._fetch_btn.setEnabled(True)
        self._fetch_btn.setText("Получить информацию")
        self._clear_info()
        lbl = QLabel(f"Ошибка подключения: {error}")
        lbl.setStyleSheet("color: #DC2626; padding: 20px;")
        lbl.setWordWrap(True)
        self._info_layout.addWidget(lbl)
        self._info_layout.addStretch()

    @Slot(str)
    def _on_info_received(self, raw: str) -> None:
        """Парсинг и отображение информации."""
        self._fetch_btn.setEnabled(True)
        self._fetch_btn.setText("Получить информацию")
        self._clear_info()

        sections = self._parse_sections(raw)

        # --- OS ---
        os_group = self._create_group("Операционная система")
        os_form = QFormLayout()
        os_form.setSpacing(4)

        os_data = sections.get("OS", "")
        os_name = self._extract(os_data, r'PRETTY_NAME="([^"]+)"')
        os_id = self._extract(os_data, r'^ID=(.+)$')
        os_version = self._extract(os_data, r'VERSION_ID="([^"]+)"')

        os_form.addRow("Дистрибутив:", self._val_label(os_name or "N/A"))
        if os_id:
            os_form.addRow("ID:", self._val_label(os_id))
        if os_version:
            os_form.addRow("Версия:", self._val_label(os_version))

        hostname = sections.get("HOSTNAME", "").strip()
        if hostname:
            os_form.addRow("Hostname:", self._val_label(hostname))

        kernel = sections.get("KERNEL", "").strip()
        if kernel:
            os_form.addRow("Ядро:", self._val_label(kernel))

        os_group.setLayout(os_form)
        self._info_layout.addWidget(os_group)

        # --- CPU ---
        cpu_group = self._create_group("Процессор")
        cpu_form = QFormLayout()
        cpu_form.setSpacing(4)

        cpu_data = sections.get("CPU", "")
        cpu_model = self._extract(cpu_data, r'Model name:\s*(.+)')
        cpu_cores = self._extract(cpu_data, r'CPU\(s\):\s*(\d+)')
        cpu_arch = self._extract(cpu_data, r'Architecture:\s*(\S+)')

        if cpu_model:
            cpu_form.addRow("Модель:", self._val_label(cpu_model))
        if cpu_cores:
            cpu_form.addRow("Ядра:", self._val_label(cpu_cores))
        if cpu_arch:
            cpu_form.addRow("Архитектура:", self._val_label(cpu_arch))

        cpu_group.setLayout(cpu_form)
        self._info_layout.addWidget(cpu_group)

        # --- RAM ---
        mem_group = self._create_group("Память")
        mem_form = QFormLayout()
        mem_form.setSpacing(4)

        mem_data = sections.get("MEM", "")
        mem_match = re.search(
            r'Mem:\s+(\d+)\s+(\d+)\s+(\d+)', mem_data,
        )
        if mem_match:
            total = int(mem_match.group(1))
            used = int(mem_match.group(2))
            pct = int(used / total * 100) if total else 0

            mem_form.addRow(
                "Использовано:",
                self._val_label(f"{used} / {total} MB ({pct}%)"),
            )

            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(pct)
            bar.setFixedHeight(18)
            bar.setStyleSheet(self._progress_style(pct))
            mem_form.addRow("", bar)

        swap_match = re.search(
            r'Swap:\s+(\d+)\s+(\d+)', mem_data,
        )
        if swap_match:
            s_total = int(swap_match.group(1))
            s_used = int(swap_match.group(2))
            if s_total > 0:
                mem_form.addRow(
                    "Swap:",
                    self._val_label(f"{s_used} / {s_total} MB"),
                )

        mem_group.setLayout(mem_form)
        self._info_layout.addWidget(mem_group)

        # --- Диски ---
        disk_group = self._create_group("Диски")
        disk_layout = QVBoxLayout()
        disk_layout.setSpacing(4)

        disk_data = sections.get("DISK", "")
        disk_lines = disk_data.strip().splitlines()
        for line in disk_lines[1:]:  # пропускаем заголовок
            parts = line.split()
            if len(parts) < 5:
                continue
            # df -h: Filesystem Size Used Avail Use% Mounted
            mount = parts[-1]
            size = parts[1] if len(parts) >= 6 else "?"
            used = parts[2] if len(parts) >= 6 else "?"
            pct_str = parts[4] if len(parts) >= 6 else parts[-2]
            pct_str = pct_str.replace("%", "")

            try:
                pct = int(pct_str)
            except ValueError:
                continue

            row = QHBoxLayout()
            row.addWidget(self._val_label(f"{mount}"), stretch=0)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(pct)
            bar.setFixedHeight(16)
            bar.setStyleSheet(self._progress_style(pct))
            row.addWidget(bar, stretch=1)
            row.addWidget(
                self._val_label(f"{used}/{size} ({pct}%)"), stretch=0,
            )

            disk_layout.addLayout(row)

        disk_group.setLayout(disk_layout)
        self._info_layout.addWidget(disk_group)

        # --- Uptime ---
        uptime_group = self._create_group("Нагрузка")
        uptime_form = QFormLayout()
        uptime_form.setSpacing(4)

        uptime_data = sections.get("UPTIME", "").strip()
        if uptime_data:
            # Парсим uptime
            up_match = re.search(r'up\s+(.+?),\s+\d+ user', uptime_data)
            if up_match:
                uptime_form.addRow(
                    "Uptime:", self._val_label(up_match.group(1).strip()),
                )

            load_match = re.search(
                r'load average:\s*([\d.,]+),\s*([\d.,]+),\s*([\d.,]+)',
                uptime_data,
            )
            if load_match:
                la = (
                    f"{load_match.group(1)} / "
                    f"{load_match.group(2)} / "
                    f"{load_match.group(3)}"
                )
                uptime_form.addRow(
                    "Load Average:", self._val_label(la),
                )

        uptime_group.setLayout(uptime_form)
        self._info_layout.addWidget(uptime_group)

        # --- IP ---
        ip_group = self._create_group("Сеть")
        ip_form = QFormLayout()
        ip_form.setSpacing(4)

        ip_data = sections.get("IP", "").strip()
        if ip_data:
            for line in ip_data.splitlines():
                line = line.strip()
                if not line or line.startswith("lo"):
                    continue
                # ip -br addr: iface STATE ip/mask
                ip_match = re.match(
                    r'(\S+)\s+\S+\s+([\d./]+)', line,
                )
                if ip_match:
                    ip_form.addRow(
                        f"{ip_match.group(1)}:",
                        self._val_label(ip_match.group(2)),
                    )

        ip_group.setLayout(ip_form)
        self._info_layout.addWidget(ip_group)

        self._info_layout.addStretch()

    # --- Утилиты ---

    @staticmethod
    def _parse_sections(raw: str) -> dict[str, str]:
        """Разбить вывод на секции по маркерам ===NAME===."""
        sections: dict[str, str] = {}
        current_key = ""
        current_lines: list[str] = []

        for line in raw.splitlines():
            m = re.match(r'^===(\w+)===$', line)
            if m:
                if current_key:
                    sections[current_key] = "\n".join(current_lines)
                current_key = m.group(1)
                current_lines = []
            else:
                current_lines.append(line)

        if current_key:
            sections[current_key] = "\n".join(current_lines)

        return sections

    @staticmethod
    def _extract(text: str, pattern: str) -> str:
        """Извлечь первое совпадение regex."""
        m = re.search(pattern, text, re.MULTILINE)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _create_group(title: str) -> QGroupBox:
        """Создать стилизованный QGroupBox."""
        group = QGroupBox(title)
        group.setStyleSheet(
            "QGroupBox { font-weight: bold; font-size: 13px;"
            " border: 1px solid #E5E7EB; border-radius: 6px;"
            " margin-top: 10px; padding-top: 14px; }"
            "QGroupBox::title { subcontrol-origin: margin;"
            " left: 10px; padding: 0 6px; }"
        )
        return group

    @staticmethod
    def _val_label(text: str) -> QLabel:
        """Создать label для значения."""
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size: 13px; color: #374151;")
        lbl.setTextInteractionFlags(
            lbl.textInteractionFlags()
            | lbl.textInteractionFlags().TextSelectableByMouse
        )
        return lbl

    @staticmethod
    def _progress_style(pct: int) -> str:
        """Стиль прогресс-бара в зависимости от процента."""
        if pct >= 90:
            color = "#DC2626"
        elif pct >= 70:
            color = "#F59E0B"
        else:
            color = "#10B981"

        return (
            f"QProgressBar {{ border: 1px solid #D4D4D8;"
            f" border-radius: 4px; text-align: center;"
            f" font-size: 11px; background: #F3F4F6; }}"
            f"QProgressBar::chunk {{ background: {color};"
            f" border-radius: 3px; }}"
        )

    def cleanup(self) -> None:
        """Очистка при закрытии."""
        self._thread_pool.waitForDone(1000)
