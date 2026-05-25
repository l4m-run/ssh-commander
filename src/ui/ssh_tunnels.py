# -*- coding: utf-8 -*-
"""SSH-туннели: GUI для управления port forwarding через SSH.

Поддерживает Local (-L) и Remote (-R) forwarding через paramiko.
"""

from __future__ import annotations

import logging
import select
import socket
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.crypto import crypto
from src.models.connection import Connection

if TYPE_CHECKING:
    from src.core.database import Database

logger = logging.getLogger(__name__)


@dataclass
class TunnelInfo:
    """Информация об активном туннеле."""

    tunnel_id: int = 0
    server_name: str = ""
    tunnel_type: str = "Local"  # Local / Remote
    local_port: int = 0
    remote_host: str = "localhost"
    remote_port: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now().strftime("%H:%M:%S"),
    )
    # Runtime
    thread: threading.Thread | None = field(default=None, repr=False)
    transport: object | None = field(default=None, repr=False)
    stop_event: threading.Event = field(
        default_factory=threading.Event, repr=False,
    )
    server_socket: socket.socket | None = field(default=None, repr=False)


class _TunnelSignals(QObject):
    """Сигналы для уведомлений о туннелях."""

    tunnel_started = Signal(int)  # tunnel_id
    tunnel_error = Signal(int, str)  # tunnel_id, error
    tunnel_stopped = Signal(int)  # tunnel_id


class SshTunnelWidget(QWidget):
    """Виджет управления SSH-туннелями.

    Layout:
    +-----------------------------------------------+
    |  [Форма создания туннеля]                     |
    +-----------------------------------------------+
    |  Таблица активных туннелей                    |
    |  Сервер | Тип | Локальный | Удалённый | ...   |
    +-----------------------------------------------+
    """

    def __init__(
        self,
        app_db: Database,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_db = app_db
        self._tunnels: dict[int, TunnelInfo] = {}
        self._next_id = 1
        self._signals = _TunnelSignals()

        self._signals.tunnel_started.connect(self._on_tunnel_started)
        self._signals.tunnel_error.connect(self._on_tunnel_error)
        self._signals.tunnel_stopped.connect(self._on_tunnel_stopped)

        self._setup_ui()
        self._refresh_connections()

        # Таймер для обновления статуса
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_table)
        self._timer.start(5000)

    def _setup_ui(self) -> None:
        """Создание UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # --- Форма создания ---
        form_group = QGroupBox("Новый туннель")
        form_group.setStyleSheet(
            "QGroupBox { font-weight: bold; font-size: 13px;"
            " border: 1px solid #E5E7EB; border-radius: 6px;"
            " margin-top: 10px; padding-top: 14px; }"
            "QGroupBox::title { subcontrol-origin: margin;"
            " left: 10px; padding: 0 6px; }"
        )
        form_layout = QVBoxLayout(form_group)

        # Сервер
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Сервер:"))
        self._server_combo = QComboBox()
        self._server_combo.setMinimumWidth(250)
        row1.addWidget(self._server_combo, stretch=1)

        row1.addWidget(QLabel("Тип:"))
        self._type_combo = QComboBox()
        self._type_combo.addItems(["Local (-L)", "Remote (-R)"])
        row1.addWidget(self._type_combo)
        form_layout.addLayout(row1)

        # Порты
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Локальный порт:"))
        self._local_port = QSpinBox()
        self._local_port.setRange(1, 65535)
        self._local_port.setValue(8080)
        row2.addWidget(self._local_port)

        row2.addWidget(QLabel("Удалённый хост:"))
        self._remote_host = QLineEdit("localhost")
        self._remote_host.setMaximumWidth(200)
        row2.addWidget(self._remote_host)

        row2.addWidget(QLabel("Удалённый порт:"))
        self._remote_port = QSpinBox()
        self._remote_port.setRange(1, 65535)
        self._remote_port.setValue(80)
        row2.addWidget(self._remote_port)

        self._connect_btn = QPushButton("Подключить")
        self._connect_btn.setStyleSheet(
            "QPushButton { background: #10B981; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 20px; font-weight: bold; }"
            "QPushButton:hover { background: #059669; }"
        )
        self._connect_btn.clicked.connect(self._create_tunnel)
        row2.addWidget(self._connect_btn)

        form_layout.addLayout(row2)
        layout.addWidget(form_group)

        # --- Таблица активных ---
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "Сервер", "Тип", "Локальный порт",
            "Удалённый хост", "Удалённый порт",
            "Создан", "Действие",
        ])
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)

        layout.addWidget(self._table, stretch=1)

        # Статус
        self._status = QLabel("Активных туннелей: 0")
        self._status.setStyleSheet("color: #6B7280; font-size: 12px;")
        layout.addWidget(self._status)

    def _refresh_connections(self) -> None:
        """Заполнить комбобокс серверов."""
        self._server_combo.clear()
        connections = self._app_db.get_all_connections()
        for conn in connections:
            label = f"{conn.name} ({conn.host}:{conn.port})"
            self._server_combo.addItem(label, conn.id)

    def _create_tunnel(self) -> None:
        """Создать новый SSH-туннель."""
        conn_id = self._server_combo.currentData()
        if conn_id is None:
            QMessageBox.information(
                self, "Туннель", "Выберите сервер.",
            )
            return

        conn = self._app_db.get_connection(conn_id)
        if not conn:
            return

        local_port = self._local_port.value()
        remote_host = self._remote_host.text().strip() or "localhost"
        remote_port = self._remote_port.value()
        tunnel_type = "Local" if "Local" in self._type_combo.currentText() else "Remote"

        # Проверка: порт уже используется?
        for t in self._tunnels.values():
            if t.local_port == local_port:
                QMessageBox.warning(
                    self, "Туннель",
                    f"Порт {local_port} уже используется туннелем.",
                )
                return

        tunnel_id = self._next_id
        self._next_id += 1

        info = TunnelInfo(
            tunnel_id=tunnel_id,
            server_name=conn.name,
            tunnel_type=tunnel_type,
            local_port=local_port,
            remote_host=remote_host,
            remote_port=remote_port,
        )
        self._tunnels[tunnel_id] = info

        # Запуск в отдельном потоке
        if tunnel_type == "Local":
            thread = threading.Thread(
                target=self._run_local_forward,
                args=(conn, info),
                daemon=True,
            )
        else:
            thread = threading.Thread(
                target=self._run_remote_forward,
                args=(conn, info),
                daemon=True,
            )

        info.thread = thread
        thread.start()

        self._refresh_table()
        self._status.setText(
            f"Активных туннелей: {len(self._tunnels)}"
        )

    def _run_local_forward(
        self, conn: Connection, info: TunnelInfo,
    ) -> None:
        """Local port forwarding: localhost:local_port -> remote:remote_port."""
        import paramiko

        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            kwargs: dict = {
                "hostname": conn.host,
                "port": conn.port,
                "username": conn.username,
                "timeout": 15,
            }
            if conn.encrypted_password:
                try:
                    kwargs["password"] = crypto.decrypt(
                        conn.encrypted_password
                    )
                except Exception:
                    pass
            if conn.ssh_key_path:
                kwargs["key_filename"] = conn.ssh_key_path

            client.connect(**kwargs)
            transport = client.get_transport()
            info.transport = transport

            # Открываем локальный сокет
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.settimeout(1.0)
            srv.bind(("127.0.0.1", info.local_port))
            srv.listen(5)
            info.server_socket = srv

            self._signals.tunnel_started.emit(info.tunnel_id)

            while not info.stop_event.is_set():
                try:
                    client_sock, _addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                # Для каждого подключения открываем SSH-канал
                try:
                    channel = transport.open_channel(
                        "direct-tcpip",
                        (info.remote_host, info.remote_port),
                        client_sock.getpeername(),
                    )
                except Exception:
                    client_sock.close()
                    continue

                # Прокси в отдельном потоке
                proxy = threading.Thread(
                    target=self._proxy_data,
                    args=(client_sock, channel, info.stop_event),
                    daemon=True,
                )
                proxy.start()

        except Exception as e:
            self._signals.tunnel_error.emit(
                info.tunnel_id, str(e),
            )
            return
        finally:
            self._signals.tunnel_stopped.emit(info.tunnel_id)

    def _run_remote_forward(
        self, conn: Connection, info: TunnelInfo,
    ) -> None:
        """Remote port forwarding: remote:remote_port -> localhost:local_port."""
        import paramiko

        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            kwargs: dict = {
                "hostname": conn.host,
                "port": conn.port,
                "username": conn.username,
                "timeout": 15,
            }
            if conn.encrypted_password:
                try:
                    kwargs["password"] = crypto.decrypt(
                        conn.encrypted_password
                    )
                except Exception:
                    pass
            if conn.ssh_key_path:
                kwargs["key_filename"] = conn.ssh_key_path

            client.connect(**kwargs)
            transport = client.get_transport()
            info.transport = transport

            transport.request_port_forward("", info.remote_port)
            self._signals.tunnel_started.emit(info.tunnel_id)

            while not info.stop_event.is_set():
                channel = transport.accept(timeout=1)
                if channel is None:
                    continue

                # Подключаемся к локальному порту
                try:
                    local_sock = socket.create_connection(
                        ("127.0.0.1", info.local_port), timeout=5,
                    )
                except Exception:
                    channel.close()
                    continue

                proxy = threading.Thread(
                    target=self._proxy_data,
                    args=(local_sock, channel, info.stop_event),
                    daemon=True,
                )
                proxy.start()

        except Exception as e:
            self._signals.tunnel_error.emit(
                info.tunnel_id, str(e),
            )
            return
        finally:
            self._signals.tunnel_stopped.emit(info.tunnel_id)

    @staticmethod
    def _proxy_data(
        sock: socket.socket,
        channel,
        stop_event: threading.Event,
    ) -> None:
        """Проксирование данных между сокетом и SSH-каналом."""
        try:
            while not stop_event.is_set():
                r, _, _ = select.select([sock, channel], [], [], 1.0)
                if sock in r:
                    data = sock.recv(32768)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in r:
                    data = channel.recv(32768)
                    if not data:
                        break
                    sock.sendall(data)
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass
            try:
                channel.close()
            except Exception:
                pass

    def _stop_tunnel(self, tunnel_id: int) -> None:
        """Остановить туннель."""
        info = self._tunnels.get(tunnel_id)
        if not info:
            return

        info.stop_event.set()

        if info.server_socket:
            try:
                info.server_socket.close()
            except Exception:
                pass

        if info.transport:
            try:
                info.transport.close()
            except Exception:
                pass

        # Ждём завершения потока
        if info.thread and info.thread.is_alive():
            info.thread.join(timeout=3)

        self._tunnels.pop(tunnel_id, None)
        self._refresh_table()
        self._status.setText(
            f"Активных туннелей: {len(self._tunnels)}"
        )

    @Slot(int)
    def _on_tunnel_started(self, tunnel_id: int) -> None:
        """Туннель успешно запущен."""
        self._refresh_table()

    @Slot(int, str)
    def _on_tunnel_error(self, tunnel_id: int, error: str) -> None:
        """Ошибка туннеля."""
        info = self._tunnels.pop(tunnel_id, None)
        self._refresh_table()
        server = info.server_name if info else "?"
        QMessageBox.warning(
            self, "Ошибка туннеля",
            f"Сервер: {server}\nОшибка: {error}",
        )

    @Slot(int)
    def _on_tunnel_stopped(self, tunnel_id: int) -> None:
        """Туннель остановлен."""
        self._tunnels.pop(tunnel_id, None)
        self._refresh_table()
        self._status.setText(
            f"Активных туннелей: {len(self._tunnels)}"
        )

    def _refresh_table(self) -> None:
        """Обновить таблицу активных туннелей."""
        self._table.setRowCount(len(self._tunnels))

        for row, (tid, info) in enumerate(self._tunnels.items()):
            self._table.setItem(
                row, 0, QTableWidgetItem(info.server_name),
            )
            self._table.setItem(
                row, 1, QTableWidgetItem(info.tunnel_type),
            )
            self._table.setItem(
                row, 2, QTableWidgetItem(str(info.local_port)),
            )
            self._table.setItem(
                row, 3, QTableWidgetItem(info.remote_host),
            )
            self._table.setItem(
                row, 4, QTableWidgetItem(str(info.remote_port)),
            )
            self._table.setItem(
                row, 5, QTableWidgetItem(info.created_at),
            )

            # Кнопка отключения
            stop_btn = QPushButton("Отключить")
            stop_btn.setStyleSheet(
                "QPushButton { background: #EF4444; color: white;"
                " border: none; border-radius: 3px;"
                " padding: 3px 10px; font-size: 11px; }"
                "QPushButton:hover { background: #DC2626; }"
            )
            stop_btn.clicked.connect(
                lambda checked, t=tid: self._stop_tunnel(t),
            )
            self._table.setCellWidget(row, 6, stop_btn)

        self._table.resizeColumnsToContents()

    def cleanup(self) -> None:
        """Остановить все туннели при закрытии."""
        self._timer.stop()
        for tid in list(self._tunnels.keys()):
            info = self._tunnels.get(tid)
            if info:
                info.stop_event.set()
                if info.server_socket:
                    try:
                        info.server_socket.close()
                    except Exception:
                        pass
                if info.transport:
                    try:
                        info.transport.close()
                    except Exception:
                        pass
        # Ждём завершения потоков
        for info in self._tunnels.values():
            if info.thread and info.thread.is_alive():
                info.thread.join(timeout=2)
        self._tunnels.clear()
