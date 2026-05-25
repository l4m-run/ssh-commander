# -*- coding: utf-8 -*-
"""Сетевые утилиты: ping, traceroute, DNS lookup, проверка порта.

Выполняются локально или через SSH на удалённом сервере.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import threading
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.crypto import crypto
from src.models.connection import Connection

if TYPE_CHECKING:
    from src.core.database import Database

logger = logging.getLogger(__name__)


class _NetSignals(QObject):
    """Сигналы сетевых утилит."""

    output = Signal(str)  # текстовый результат
    progress = Signal(int, int, str)  # current, total, info
    finished = Signal()


class _NetWorker(QRunnable):
    """Воркер для выполнения сетевой команды с возможностью остановки."""

    def __init__(
        self,
        command: str,
        conn: Connection | None = None,
    ) -> None:
        super().__init__()
        self.signals = _NetSignals()
        self._command = command
        self._conn = conn
        self._stop_event = threading.Event()
        self._process: subprocess.Popen | None = None
        self._ssh_client = None
        self.setAutoDelete(True)

    def stop(self) -> None:
        """Остановить выполнение."""
        self._stop_event.set()
        # Убить локальный процесс
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
        # Закрыть SSH
        if self._ssh_client:
            try:
                self._ssh_client.close()
            except Exception:
                pass

    @Slot()
    def run(self) -> None:
        """Выполнить команду локально или через SSH."""
        try:
            if self._conn:
                result = self._run_ssh()
            else:
                result = self._run_local()

            if self._stop_event.is_set():
                result = "Остановлено пользователем."

            try:
                self.signals.output.emit(result)
            except RuntimeError:
                pass
        except Exception as e:
            msg = "Остановлено." if self._stop_event.is_set() else f"Ошибка: {e}"
            try:
                self.signals.output.emit(msg)
            except RuntimeError:
                pass
        finally:
            try:
                self.signals.finished.emit()
            except RuntimeError:
                pass

    def _run_local(self) -> str:
        """Выполнить локально через Popen."""
        self._process = subprocess.Popen(
            self._command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            out, err = self._process.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            self._process.kill()
            out, err = self._process.communicate()
        return out if out else err

    def _run_ssh(self) -> str:
        """Выполнить через SSH."""
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._ssh_client = client

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
        _, stdout, stderr = client.exec_command(self._command, timeout=120)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        client.close()
        self._ssh_client = None
        return out if out else err


class _PortScanWorker(QRunnable):
    """Воркер для сканирования портов с прогрессом и остановкой."""

    def __init__(
        self,
        host: str,
        start_port: int,
        end_port: int,
        timeout: float = 0.5,
    ) -> None:
        super().__init__()
        self.signals = _NetSignals()
        self._host = host
        self._start = start_port
        self._end = end_port
        self._timeout = timeout
        self._stop_event = threading.Event()
        self.setAutoDelete(True)

    def stop(self) -> None:
        """Остановить сканирование."""
        self._stop_event.set()

    @Slot()
    def run(self) -> None:
        """Сканировать порты."""
        results = []
        total = self._end - self._start + 1

        for i, port in enumerate(range(self._start, self._end + 1)):
            if self._stop_event.is_set():
                self.signals.progress.emit(i, total, "Остановлено")
                break

            # Прогресс каждые 10 портов
            if i % 10 == 0:
                self.signals.progress.emit(
                    i, total, f"Сканирование {port}...",
                )

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(self._timeout)
                code = sock.connect_ex((self._host, port))
                if code == 0:
                    try:
                        service = socket.getservbyport(port)
                    except OSError:
                        service = ""
                    results.append(f"  {port}/tcp  open  {service}")
                    # Сразу показываем найденный порт
                    self.signals.progress.emit(
                        i, total,
                        f"Найден: {port}/tcp ({service})" if service
                        else f"Найден: {port}/tcp",
                    )
                sock.close()
            except Exception:
                pass

        # Финальный прогресс
        self.signals.progress.emit(total, total, "Завершено")

        if self._stop_event.is_set():
            header = (
                f"Сканирование ОСТАНОВЛЕНО. "
                f"Проверено {i}/{total} портов.\n"
            )
        else:
            header = (
                f"Сканирование завершено. "
                f"Хост: {self._host} ({self._start}-{self._end})\n"
            )

        if results:
            self.signals.output.emit(
                header + f"Открытых портов: {len(results)}\n\n"
                + "\n".join(results)
            )
        else:
            self.signals.output.emit(header + "Открытых портов не найдено.")

        self.signals.finished.emit()


class NetworkToolsWidget(QWidget):
    """Виджет сетевых утилит.

    Вкладки: Ping | Traceroute | DNS | Порты
    """

    def __init__(
        self,
        app_db: Database,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_db = app_db
        self._thread_pool = QThreadPool()
        self._thread_pool.setMaxThreadCount(5)
        self._scan_worker: _PortScanWorker | None = None
        self._ping_worker: _NetWorker | None = None
        self._trace_worker: _NetWorker | None = None
        self._dns_worker: _NetWorker | None = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Создание UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Общие настройки: откуда выполнять
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Выполнять:"))

        self._local_radio = QRadioButton("Локально")
        self._local_radio.setChecked(True)
        source_row.addWidget(self._local_radio)

        self._remote_radio = QRadioButton("Через SSH:")
        source_row.addWidget(self._remote_radio)

        self._server_combo = QComboBox()
        self._server_combo.setMinimumWidth(250)
        self._refresh_connections()
        source_row.addWidget(self._server_combo, stretch=1)

        layout.addLayout(source_row)

        # Вкладки утилит
        tabs = QTabWidget()

        tabs.addTab(self._create_ping_tab(), "Ping")
        tabs.addTab(self._create_traceroute_tab(), "Traceroute")
        tabs.addTab(self._create_dns_tab(), "DNS Lookup")
        tabs.addTab(self._create_port_tab(), "Порты")

        layout.addWidget(tabs, stretch=1)

    def _refresh_connections(self) -> None:
        """Заполнить комбобокс серверов."""
        self._server_combo.clear()
        connections = self._app_db.get_all_connections()
        for conn in connections:
            label = f"{conn.name} ({conn.host})"
            self._server_combo.addItem(label, conn.id)

    def _get_conn(self) -> Connection | None:
        """Получить SSH-подключение если выбран remote."""
        if not self._remote_radio.isChecked():
            return None
        conn_id = self._server_combo.currentData()
        if conn_id is None:
            return None
        return self._app_db.get_connection(conn_id)

    # --- Ping ---

    def _create_ping_tab(self) -> QWidget:
        """Вкладка Ping."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Параметры
        params = QHBoxLayout()
        params.addWidget(QLabel("Хост:"))
        self._ping_host = QLineEdit()
        self._ping_host.setPlaceholderText("example.com или 8.8.8.8")
        self._ping_host.returnPressed.connect(self._run_ping)
        params.addWidget(self._ping_host, stretch=1)

        params.addWidget(QLabel("Пакетов:"))
        self._ping_count = QSpinBox()
        self._ping_count.setRange(1, 100)
        self._ping_count.setValue(4)
        params.addWidget(self._ping_count)

        self._ping_btn = QPushButton("Ping")
        self._ping_btn.setStyleSheet(
            "QPushButton { background: #10B981; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #059669; }"
        )
        self._ping_btn.clicked.connect(self._run_ping)
        params.addWidget(self._ping_btn)

        self._ping_stop_btn = QPushButton("Стоп")
        self._ping_stop_btn.setStyleSheet(
            "QPushButton { background: #6B7280; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #4B5563; }"
        )
        self._ping_stop_btn.setEnabled(False)
        self._ping_stop_btn.clicked.connect(self._stop_ping)
        params.addWidget(self._ping_stop_btn)

        layout.addLayout(params)

        self._ping_output = QPlainTextEdit()
        self._ping_output.setReadOnly(True)
        self._ping_output.setStyleSheet(
            "QPlainTextEdit { font-family: monospace; font-size: 13px; }"
        )
        layout.addWidget(self._ping_output, stretch=1)

        return widget

    def _run_ping(self) -> None:
        """Запустить ping."""
        host = self._ping_host.text().strip()
        if not host:
            return
        count = self._ping_count.value()
        cmd = f"ping -c {count} {host}"

        self._ping_output.setPlainText(f"Выполняется: {cmd}...")
        self._ping_btn.setEnabled(False)
        self._ping_stop_btn.setEnabled(True)

        worker = _NetWorker(cmd, self._get_conn())
        worker.signals.output.connect(self._ping_output.setPlainText)
        worker.signals.finished.connect(self._on_ping_finished)
        self._ping_worker = worker
        self._thread_pool.start(worker)

    def _stop_ping(self) -> None:
        """Остановить ping."""
        if self._ping_worker:
            self._ping_worker.stop()

    def _on_ping_finished(self) -> None:
        """Ping завершён."""
        self._ping_btn.setEnabled(True)
        self._ping_stop_btn.setEnabled(False)
        self._ping_worker = None

    # --- Traceroute ---

    def _create_traceroute_tab(self) -> QWidget:
        """Вкладка Traceroute."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        params = QHBoxLayout()
        params.addWidget(QLabel("Хост:"))
        self._trace_host = QLineEdit()
        self._trace_host.setPlaceholderText("example.com")
        self._trace_host.returnPressed.connect(self._run_traceroute)
        params.addWidget(self._trace_host, stretch=1)

        self._trace_btn = QPushButton("Traceroute")
        self._trace_btn.setStyleSheet(
            "QPushButton { background: #3B82F6; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #2563EB; }"
        )
        self._trace_btn.clicked.connect(self._run_traceroute)
        params.addWidget(self._trace_btn)

        self._trace_stop_btn = QPushButton("Стоп")
        self._trace_stop_btn.setStyleSheet(
            "QPushButton { background: #6B7280; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #4B5563; }"
        )
        self._trace_stop_btn.setEnabled(False)
        self._trace_stop_btn.clicked.connect(self._stop_traceroute)
        params.addWidget(self._trace_stop_btn)

        layout.addLayout(params)

        self._trace_output = QPlainTextEdit()
        self._trace_output.setReadOnly(True)
        self._trace_output.setStyleSheet(
            "QPlainTextEdit { font-family: monospace; font-size: 13px; }"
        )
        layout.addWidget(self._trace_output, stretch=1)

        return widget

    def _run_traceroute(self) -> None:
        """Запустить traceroute (fallback: tracepath, mtr)."""
        host = self._trace_host.text().strip()
        if not host:
            return

        # traceroute -> tracepath -> mtr (первый доступный)
        cmd = (
            "command -v traceroute > /dev/null 2>&1 && traceroute {h} || "
            "(command -v tracepath > /dev/null 2>&1 && tracepath {h} || "
            "(command -v mtr > /dev/null 2>&1 && mtr --report --report-cycles 3 {h} || "
            "echo 'Не найден traceroute/tracepath/mtr. "
            "Установите: sudo apt install traceroute'))"
        ).format(h=host)

        self._trace_output.setPlainText(f"Выполняется traceroute {host}...")
        self._trace_btn.setEnabled(False)
        self._trace_stop_btn.setEnabled(True)

        worker = _NetWorker(cmd, self._get_conn())
        worker.signals.output.connect(self._trace_output.setPlainText)
        worker.signals.finished.connect(self._on_trace_finished)
        self._trace_worker = worker
        self._thread_pool.start(worker)

    def _stop_traceroute(self) -> None:
        """Остановить traceroute."""
        if self._trace_worker:
            self._trace_worker.stop()

    def _on_trace_finished(self) -> None:
        """Traceroute завершён."""
        self._trace_btn.setEnabled(True)
        self._trace_stop_btn.setEnabled(False)
        self._trace_worker = None

    # --- DNS ---

    def _create_dns_tab(self) -> QWidget:
        """Вкладка DNS Lookup."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        params = QHBoxLayout()
        params.addWidget(QLabel("Хост:"))
        self._dns_host = QLineEdit()
        self._dns_host.setPlaceholderText("example.com")
        self._dns_host.returnPressed.connect(self._run_dns)
        params.addWidget(self._dns_host, stretch=1)

        params.addWidget(QLabel("Тип:"))
        self._dns_type = QComboBox()
        self._dns_type.addItems(["A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA", "PTR"])
        params.addWidget(self._dns_type)

        self._dns_btn = QPushButton("Lookup")
        self._dns_btn.setStyleSheet(
            "QPushButton { background: #8B5CF6; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #7C3AED; }"
        )
        self._dns_btn.clicked.connect(self._run_dns)
        params.addWidget(self._dns_btn)

        self._dns_stop_btn = QPushButton("Стоп")
        self._dns_stop_btn.setStyleSheet(
            "QPushButton { background: #6B7280; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #4B5563; }"
        )
        self._dns_stop_btn.setEnabled(False)
        self._dns_stop_btn.clicked.connect(self._stop_dns)
        params.addWidget(self._dns_stop_btn)

        layout.addLayout(params)

        self._dns_output = QPlainTextEdit()
        self._dns_output.setReadOnly(True)
        self._dns_output.setStyleSheet(
            "QPlainTextEdit { font-family: monospace; font-size: 13px; }"
        )
        layout.addWidget(self._dns_output, stretch=1)

        return widget

    def _run_dns(self) -> None:
        """Запустить DNS lookup."""
        host = self._dns_host.text().strip()
        if not host:
            return
        record_type = self._dns_type.currentText()

        # Пробуем dig, если нет - nslookup
        cmd = f"dig {host} {record_type} +short 2>/dev/null || nslookup -type={record_type} {host}"

        self._dns_output.setPlainText(f"Запрос: {host} ({record_type})...")
        self._dns_btn.setEnabled(False)
        self._dns_stop_btn.setEnabled(True)

        worker = _NetWorker(cmd, self._get_conn())
        worker.signals.output.connect(self._dns_output.setPlainText)
        worker.signals.finished.connect(self._on_dns_finished)
        self._dns_worker = worker
        self._thread_pool.start(worker)

    def _stop_dns(self) -> None:
        """Остановить DNS lookup."""
        if self._dns_worker:
            self._dns_worker.stop()

    def _on_dns_finished(self) -> None:
        """DNS lookup завершён."""
        self._dns_btn.setEnabled(True)
        self._dns_stop_btn.setEnabled(False)
        self._dns_worker = None

    # --- Порты ---

    def _create_port_tab(self) -> QWidget:
        """Вкладка проверки портов."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        params = QHBoxLayout()
        params.addWidget(QLabel("Хост:"))
        self._port_host = QLineEdit()
        self._port_host.setPlaceholderText("example.com")
        params.addWidget(self._port_host, stretch=1)

        params.addWidget(QLabel("Порт:"))
        self._port_single = QSpinBox()
        self._port_single.setRange(1, 65535)
        self._port_single.setValue(80)
        params.addWidget(self._port_single)

        check_btn = QPushButton("Проверить")
        check_btn.setStyleSheet(
            "QPushButton { background: #F59E0B; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #D97706; }"
        )
        check_btn.clicked.connect(self._check_port)
        params.addWidget(check_btn)

        layout.addLayout(params)

        # Сканер диапазона
        scan_row = QHBoxLayout()
        scan_row.addWidget(QLabel("Диапазон:"))

        self._port_start = QSpinBox()
        self._port_start.setRange(1, 65535)
        self._port_start.setValue(1)
        scan_row.addWidget(self._port_start)

        scan_row.addWidget(QLabel("-"))

        self._port_end = QSpinBox()
        self._port_end.setRange(1, 65535)
        self._port_end.setValue(1024)
        scan_row.addWidget(self._port_end)

        self._scan_btn = QPushButton("Сканировать")
        self._scan_btn.setStyleSheet(
            "QPushButton { background: #EF4444; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #DC2626; }"
        )
        self._scan_btn.clicked.connect(self._scan_ports)
        scan_row.addWidget(self._scan_btn)

        self._stop_scan_btn = QPushButton("Остановить")
        self._stop_scan_btn.setStyleSheet(
            "QPushButton { background: #6B7280; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #4B5563; }"
        )
        self._stop_scan_btn.setEnabled(False)
        self._stop_scan_btn.clicked.connect(self._stop_scan)
        scan_row.addWidget(self._stop_scan_btn)

        scan_row.addStretch()
        layout.addLayout(scan_row)

        # Прогресс-бар
        progress_row = QHBoxLayout()
        self._scan_progress = QProgressBar()
        self._scan_progress.setMaximumHeight(18)
        self._scan_progress.setTextVisible(True)
        self._scan_progress.setValue(0)
        progress_row.addWidget(self._scan_progress)

        self._scan_status = QLabel("")
        self._scan_status.setStyleSheet("font-size: 12px; color: #6B7280;")
        progress_row.addWidget(self._scan_status)

        layout.addLayout(progress_row)

        self._port_output = QPlainTextEdit()
        self._port_output.setReadOnly(True)
        self._port_output.setStyleSheet(
            "QPlainTextEdit { font-family: monospace; font-size: 13px; }"
        )
        layout.addWidget(self._port_output, stretch=1)

        return widget

    def _check_port(self) -> None:
        """Проверить один порт."""
        host = self._port_host.text().strip()
        if not host:
            return
        port = self._port_single.value()

        self._port_output.setPlainText(f"Проверка {host}:{port}...")

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            start = time.monotonic()
            code = sock.connect_ex((host, port))
            elapsed = time.monotonic() - start
            sock.close()

            if code == 0:
                try:
                    service = socket.getservbyport(port)
                except OSError:
                    service = "unknown"
                result = (
                    f"Порт {port} на {host}: ОТКРЫТ ({service})\n"
                    f"Время отклика: {elapsed * 1000:.0f}мс"
                )
            else:
                result = f"Порт {port} на {host}: ЗАКРЫТ"
        except socket.timeout:
            result = f"Порт {port} на {host}: ТАЙМАУТ"
        except Exception as e:
            result = f"Ошибка: {e}"

        self._port_output.setPlainText(result)

    def _scan_ports(self) -> None:
        """Сканировать диапазон портов."""
        host = self._port_host.text().strip()
        if not host:
            return
        start_port = self._port_start.value()
        end_port = self._port_end.value()

        if start_port > end_port:
            start_port, end_port = end_port, start_port

        if end_port - start_port > 5000:
            QMessageBox.warning(
                self, "Сканер",
                "Максимальный диапазон: 5000 портов.",
            )
            return

        total = end_port - start_port + 1
        self._scan_progress.setMaximum(total)
        self._scan_progress.setValue(0)
        self._scan_status.setText(f"0/{total}")
        self._port_output.setPlainText(
            f"Сканирование {host} ({start_port}-{end_port})..."
        )

        # UI: кнопки
        self._scan_btn.setEnabled(False)
        self._stop_scan_btn.setEnabled(True)

        worker = _PortScanWorker(host, start_port, end_port)
        worker.signals.progress.connect(self._on_scan_progress)
        worker.signals.output.connect(self._port_output.setPlainText)
        worker.signals.finished.connect(self._on_scan_finished)
        self._scan_worker = worker
        self._thread_pool.start(worker)

    @Slot(int, int, str)
    def _on_scan_progress(
        self, current: int, total: int, info: str,
    ) -> None:
        """Обновить прогресс сканирования."""
        self._scan_progress.setValue(current)
        self._scan_status.setText(f"{current}/{total}  {info}")

    def _on_scan_finished(self) -> None:
        """Сканирование завершено."""
        self._scan_btn.setEnabled(True)
        self._stop_scan_btn.setEnabled(False)
        self._scan_worker = None

    def _stop_scan(self) -> None:
        """Остановить сканирование."""
        if self._scan_worker:
            self._scan_worker.stop()
            self._scan_status.setText("Остановка...")

    def cleanup(self) -> None:
        """Очистка при закрытии."""
        for worker in (
            self._ping_worker, self._trace_worker,
            self._dns_worker, self._scan_worker,
        ):
            if worker:
                worker.stop()
        self._thread_pool.waitForDone(2000)
