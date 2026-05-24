# -*- coding: utf-8 -*-
"""Виджет эмуляции терминала.

Использует pyte для эмуляции VT100 и QPainter для рендеринга.
Принимает данные от SSH-сессии и отображает их как терминал.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pyte
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QAbstractScrollArea, QScrollBar, QWidget

from src.core.config import config

if TYPE_CHECKING:
    from src.core.ssh_manager import SSHSession

logger = logging.getLogger(__name__)

# Таблица цветов ANSI (xterm-256color, стандартные 16)
ANSI_COLORS = {
    "black": "#45475A",
    "red": "#F38BA8",
    "green": "#A6E3A1",
    "yellow": "#F9E2AF",
    "blue": "#89B4FA",
    "magenta": "#F5C2E7",
    "cyan": "#94E2D5",
    "white": "#BAC2DE",
    # Яркие варианты
    "brightblack": "#585B70",
    "brightred": "#F38BA8",
    "brightgreen": "#A6E3A1",
    "brightyellow": "#F9E2AF",
    "brightblue": "#89B4FA",
    "brightmagenta": "#F5C2E7",
    "brightcyan": "#94E2D5",
    "brightwhite": "#A6ADC8",
}

# Цвета по умолчанию для терминала
DEFAULT_FG = "#CDD6F4"
DEFAULT_BG = "#1E1E2E"

# Маппинг Qt-клавиш в escape-последовательности
KEY_MAP = {
    Qt.Key.Key_Up: b"\x1b[A",
    Qt.Key.Key_Down: b"\x1b[B",
    Qt.Key.Key_Right: b"\x1b[C",
    Qt.Key.Key_Left: b"\x1b[D",
    Qt.Key.Key_Home: b"\x1b[H",
    Qt.Key.Key_End: b"\x1b[F",
    Qt.Key.Key_PageUp: b"\x1b[5~",
    Qt.Key.Key_PageDown: b"\x1b[6~",
    Qt.Key.Key_Insert: b"\x1b[2~",
    Qt.Key.Key_Delete: b"\x1b[3~",
    Qt.Key.Key_F1: b"\x1bOP",
    Qt.Key.Key_F2: b"\x1bOQ",
    Qt.Key.Key_F3: b"\x1bOR",
    Qt.Key.Key_F4: b"\x1bOS",
    Qt.Key.Key_F5: b"\x1b[15~",
    Qt.Key.Key_F6: b"\x1b[17~",
    Qt.Key.Key_F7: b"\x1b[18~",
    Qt.Key.Key_F8: b"\x1b[19~",
    Qt.Key.Key_F9: b"\x1b[20~",
    Qt.Key.Key_F10: b"\x1b[21~",
    Qt.Key.Key_F11: b"\x1b[23~",
    Qt.Key.Key_F12: b"\x1b[24~",
    Qt.Key.Key_Backspace: b"\x7f",
    Qt.Key.Key_Tab: b"\t",
    Qt.Key.Key_Return: b"\r",
    Qt.Key.Key_Enter: b"\r",
    Qt.Key.Key_Escape: b"\x1b",
}


class TerminalWidget(QAbstractScrollArea):
    """Виджет эмуляции VT100-терминала.

    Рендерит экран pyte.Screen через QPainter.
    Обрабатывает ввод с клавиатуры и отправляет в SSH-канал.

    Signals:
        title_changed: Изменился заголовок терминала (str).
        size_changed: Изменился размер в символах (cols, rows).
    """

    title_changed = Signal(str)
    size_changed = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Шрифт терминала
        self._font = QFont(
            config.terminal_font_family,
            config.terminal_font_size,
        )
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        metrics = QFontMetricsF(self._font)
        self._char_width = metrics.horizontalAdvance("M")
        self._char_height = metrics.height()
        self._char_ascent = metrics.ascent()

        # Размер терминала в символах
        self._cols = 80
        self._rows = 24

        # pyte - эмулятор терминала
        self._screen = pyte.Screen(self._cols, self._rows)
        self._screen.set_mode(pyte.modes.LNM)  # Line feed / New line mode
        self._stream = pyte.Stream(self._screen)

        # Буфер прокрутки (история)
        self._scrollback: list[dict] = []
        self._scrollback_max = config.terminal_scrollback
        self._scroll_offset = 0

        # SSH-сессия
        self._session: SSHSession | None = None

        # Настройка виджета
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)

        # Таймер перерисовки (ограничиваем частоту)
        self._dirty = False
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setInterval(16)  # ~60 FPS
        self._repaint_timer.timeout.connect(self._do_repaint)
        self._repaint_timer.start()

        # Скроллбар
        self._setup_scrollbar()

    def _setup_scrollbar(self) -> None:
        """Настройка вертикального скроллбара."""
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scrollbar = self.verticalScrollBar()
        scrollbar.valueChanged.connect(self._on_scroll)

    def attach_session(self, session: SSHSession) -> None:
        """Привязать SSH-сессию к терминалу.

        Args:
            session: Активная SSH-сессия.
        """
        self._session = session
        session.data_received.connect(self._on_data_received)
        # Сообщаем серверу размер терминала
        session.resize_pty(self._cols, self._rows)

    def detach_session(self) -> None:
        """Отвязать SSH-сессию."""
        if self._session:
            try:
                self._session.data_received.disconnect(self._on_data_received)
            except RuntimeError:
                pass
            self._session = None

    def _on_data_received(self, data: bytes) -> None:
        """Обработка данных от SSH-сервера.

        Args:
            data: Сырые байты от сервера.
        """
        try:
            text = data.decode("utf-8", errors="replace")
            # Сохраняем строки в scrollback перед обновлением экрана
            old_lines = self._get_screen_lines()
            self._stream.feed(text)
            # Проверяем, изменились ли строки (для scrollback)
            self._update_scrollback(old_lines)
            self._dirty = True
        except Exception as e:
            logger.error("Ошибка обработки данных: %s", e)

    def _get_screen_lines(self) -> list[dict]:
        """Получить текущие строки экрана для scrollback."""
        lines = []
        for y in range(self._rows):
            line = {}
            for x in range(self._cols):
                char = self._screen.buffer[y][x]
                if char.data != " " or char.fg != "default" or char.bg != "default":
                    line[x] = char
            lines.append(line)
        return lines

    def _update_scrollback(self, old_lines: list[dict]) -> None:
        """Обновить буфер прокрутки при скролле экрана."""
        # pyte.HistoryScreen слишком сложен, используем упрощённый подход:
        # Если курсор находится в последней строке и экран прокрутился,
        # сохраняем верхнюю строку в scrollback
        pass  # Упрощённая версия для MVP

    def _do_repaint(self) -> None:
        """Перерисовка по таймеру (если есть изменения)."""
        if self._dirty:
            self._dirty = False
            self._update_scrollbar()
            self.viewport().update()

    def _update_scrollbar(self) -> None:
        """Обновить состояние скроллбара."""
        scrollbar = self.verticalScrollBar()
        total = len(self._scrollback) + self._rows
        scrollbar.setRange(0, max(0, total - self._rows))
        if self._scroll_offset == 0:
            scrollbar.setValue(scrollbar.maximum())

    def _on_scroll(self, value: int) -> None:
        """Обработка скроллинга."""
        scrollbar = self.verticalScrollBar()
        self._scroll_offset = scrollbar.maximum() - value
        self.viewport().update()

    def _resolve_color(self, color: str, is_bg: bool = False) -> QColor:
        """Преобразовать цвет pyte в QColor.

        Args:
            color: Цвет из pyte (имя, "default", или номер 256-color).
            is_bg: True если это цвет фона.

        Returns:
            QColor для рисования.
        """
        if color == "default":
            return QColor(DEFAULT_BG if is_bg else DEFAULT_FG)

        # Именованный цвет ANSI
        if color in ANSI_COLORS:
            return QColor(ANSI_COLORS[color])

        # 256-color (число)
        try:
            idx = int(color)
            if 0 <= idx <= 255:
                return self._color_from_256(idx)
        except (ValueError, TypeError):
            pass

        return QColor(DEFAULT_BG if is_bg else DEFAULT_FG)

    @staticmethod
    def _color_from_256(idx: int) -> QColor:
        """Преобразовать 256-color индекс в QColor."""
        # Стандартные 16 цветов
        base_colors = [
            "#45475A", "#F38BA8", "#A6E3A1", "#F9E2AF",
            "#89B4FA", "#F5C2E7", "#94E2D5", "#BAC2DE",
            "#585B70", "#F38BA8", "#A6E3A1", "#F9E2AF",
            "#89B4FA", "#F5C2E7", "#94E2D5", "#A6ADC8",
        ]
        if idx < 16:
            return QColor(base_colors[idx])
        # 216 цветов (6x6x6 куб)
        if idx < 232:
            idx -= 16
            b = (idx % 6) * 51
            idx //= 6
            g = (idx % 6) * 51
            r = (idx // 6) * 51
            return QColor(r, g, b)
        # Оттенки серого
        gray = 8 + (idx - 232) * 10
        return QColor(gray, gray, gray)

    def paintEvent(self, event: QPaintEvent) -> None:
        """Отрисовка содержимого терминала."""
        painter = QPainter(self.viewport())
        painter.setFont(self._font)

        # Фон терминала
        painter.fillRect(self.viewport().rect(), QColor(DEFAULT_BG))

        # Отрисовка каждого символа
        for y in range(self._rows):
            for x in range(self._cols):
                char = self._screen.buffer[y][x]
                px = x * self._char_width
                py = y * self._char_height

                # Цвета
                fg_color = self._resolve_color(char.fg)
                bg_color = self._resolve_color(char.bg, is_bg=True)

                # Инверсия при reverse
                if char.reverse:
                    fg_color, bg_color = bg_color, fg_color

                # Фон символа (только если не дефолтный)
                if char.bg != "default" or char.reverse:
                    painter.fillRect(
                        int(px), int(py),
                        int(self._char_width) + 1, int(self._char_height),
                        bg_color,
                    )

                # Символ
                if char.data and char.data != " ":
                    # Стиль шрифта
                    font = QFont(self._font)
                    if char.bold:
                        font.setBold(True)
                    if char.italics:
                        font.setItalic(True)
                    painter.setFont(font)

                    painter.setPen(fg_color)
                    painter.drawText(
                        int(px), int(py + self._char_ascent),
                        char.data,
                    )

                    if font != self._font:
                        painter.setFont(self._font)

                # Подчёркивание
                if char.underscore:
                    painter.setPen(fg_color)
                    underline_y = int(py + self._char_height - 1)
                    painter.drawLine(
                        int(px), underline_y,
                        int(px + self._char_width), underline_y,
                    )

        # Курсор
        if self.hasFocus():
            cx = self._screen.cursor.x * self._char_width
            cy = self._screen.cursor.y * self._char_height
            painter.setPen(QColor("#F5E0DC"))
            painter.drawRect(
                int(cx), int(cy),
                int(self._char_width) - 1, int(self._char_height) - 1,
            )

        painter.end()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Обработка нажатий клавиш."""
        if self._session is None:
            return

        key = event.key()
        modifiers = event.modifiers()

        # Ctrl+C, Ctrl+D и другие Ctrl-комбинации
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            # Ctrl+Shift+C - копирование (не отправляем в терминал)
            if key == Qt.Key.Key_C and modifiers & Qt.KeyboardModifier.ShiftModifier:
                self._copy_selection()
                return
            # Ctrl+Shift+V - вставка
            if key == Qt.Key.Key_V and modifiers & Qt.KeyboardModifier.ShiftModifier:
                self._paste()
                return
            # Ctrl + буква -> ASCII-код управления
            if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
                ctrl_char = bytes([key - Qt.Key.Key_A + 1])
                self._session.write(ctrl_char)
                return
            if key == Qt.Key.Key_BracketLeft:
                self._session.write(b"\x1b")
                return

        # Специальные клавиши
        if key in KEY_MAP:
            self._session.write(KEY_MAP[key])
            return

        # Обычный текст
        text = event.text()
        if text:
            self._session.write(text.encode("utf-8"))

    def _copy_selection(self) -> None:
        """Копировать выделенный текст в буфер обмена."""
        # TODO: Реализовать выделение текста мышью
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        # Пока копируем весь видимый текст
        lines = []
        for y in range(self._rows):
            line = ""
            for x in range(self._cols):
                line += self._screen.buffer[y][x].data
            lines.append(line.rstrip())
        clipboard.setText("\n".join(lines))

    def _paste(self) -> None:
        """Вставить текст из буфера обмена."""
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if text and self._session:
            # Заменяем \n на \r для терминала
            text = text.replace("\n", "\r")
            self._session.write(text.encode("utf-8"))

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Обработка изменения размера виджета."""
        super().resizeEvent(event)

        viewport = self.viewport()
        new_cols = max(1, int(viewport.width() / self._char_width))
        new_rows = max(1, int(viewport.height() / self._char_height))

        if new_cols != self._cols or new_rows != self._rows:
            self._cols = new_cols
            self._rows = new_rows

            # Пересоздаём экран pyte с новым размером
            self._screen.resize(self._rows, self._cols)

            # Сообщаем SSH-серверу новый размер
            if self._session:
                self._session.resize_pty(self._cols, self._rows)

            self.size_changed.emit(self._cols, self._rows)
            self._dirty = True

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Обработка прокрутки мышью."""
        delta = event.angleDelta().y()
        if delta > 0:
            self._scroll_offset = min(
                self._scroll_offset + 3, len(self._scrollback)
            )
        else:
            self._scroll_offset = max(self._scroll_offset - 3, 0)
        self._update_scrollbar()
        self.viewport().update()

    def sizeHint(self) -> QSize:
        """Рекомендуемый размер виджета."""
        return QSize(
            int(80 * self._char_width) + self.verticalScrollBar().width(),
            int(24 * self._char_height),
        )

    def minimumSizeHint(self) -> QSize:
        """Минимальный размер виджета."""
        return QSize(
            int(40 * self._char_width),
            int(10 * self._char_height),
        )
