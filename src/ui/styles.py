# -*- coding: utf-8 -*-
"""Стили и тема приложения.

Светлая тема с современным дизайном.
"""

# Основная палитра
COLORS = {
    "bg_primary": "#FAFBFC",
    "bg_secondary": "#F0F2F5",
    "bg_sidebar": "#FFFFFF",
    "bg_terminal": "#1E1E2E",
    "bg_input": "#FFFFFF",
    "bg_hover": "#E8ECF0",
    "bg_selected": "#D0E3FF",
    "bg_button": "#4A90D9",
    "bg_button_hover": "#357ABD",
    "bg_button_danger": "#E74C3C",
    "bg_tab_active": "#FFFFFF",
    "bg_tab_inactive": "#E8ECF0",
    "text_primary": "#1A1A2E",
    "text_secondary": "#6B7280",
    "text_on_button": "#FFFFFF",
    "text_terminal": "#CDD6F4",
    "border": "#D1D5DB",
    "border_focus": "#4A90D9",
    "accent": "#4A90D9",
    "success": "#27AE60",
    "warning": "#F39C12",
    "error": "#E74C3C",
    "scrollbar_bg": "#F0F2F5",
    "scrollbar_handle": "#C1C7CD",
}

# Шрифты
FONTS = {
    "ui": "Inter, Segoe UI, Ubuntu, sans-serif",
    "terminal": "Ubuntu Mono, JetBrains Mono, Consolas, monospace",
    "size_normal": "13px",
    "size_small": "11px",
    "size_large": "15px",
}


def get_app_stylesheet() -> str:
    """Получить основную таблицу стилей приложения (QSS)."""
    return f"""
    /* === Главное окно === */
    QMainWindow {{
        background-color: {COLORS["bg_primary"]};
        color: {COLORS["text_primary"]};
        font-family: {FONTS["ui"]};
        font-size: {FONTS["size_normal"]};
    }}

    /* === Меню и тулбар === */
    QMenuBar {{
        background-color: {COLORS["bg_sidebar"]};
        border-bottom: 1px solid {COLORS["border"]};
        padding: 2px;
    }}
    QMenuBar::item:selected {{
        background-color: {COLORS["bg_hover"]};
        border-radius: 4px;
    }}
    QMenu {{
        background-color: {COLORS["bg_sidebar"]};
        border: 1px solid {COLORS["border"]};
        border-radius: 6px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 24px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{
        background-color: {COLORS["bg_hover"]};
    }}
    QToolBar {{
        background-color: {COLORS["bg_sidebar"]};
        border-bottom: 1px solid {COLORS["border"]};
        spacing: 4px;
        padding: 4px 8px;
    }}
    QToolButton {{
        border: none;
        border-radius: 6px;
        padding: 6px 10px;
        color: {COLORS["text_primary"]};
    }}
    QToolButton:hover {{
        background-color: {COLORS["bg_hover"]};
    }}

    /* === Sidebar (QTreeWidget) === */
    QTreeWidget {{
        background-color: {COLORS["bg_sidebar"]};
        border: none;
        border-right: 1px solid {COLORS["border"]};
        outline: none;
        font-size: {FONTS["size_normal"]};
    }}
    QTreeWidget::item {{
        padding: 6px 8px;
        border-radius: 4px;
        margin: 1px 4px;
    }}
    QTreeWidget::item:hover {{
        background-color: {COLORS["bg_hover"]};
    }}
    QTreeWidget::item:selected {{
        background-color: {COLORS["bg_selected"]};
        color: {COLORS["text_primary"]};
    }}
    QTreeWidget::branch {{
        background-color: {COLORS["bg_sidebar"]};
    }}
    QHeaderView::section {{
        background-color: {COLORS["bg_sidebar"]};
        border: none;
        padding: 6px;
        font-weight: bold;
        color: {COLORS["text_secondary"]};
    }}

    /* === Вкладки === */
    QTabWidget::pane {{
        border: none;
        background-color: {COLORS["bg_primary"]};
    }}
    QTabBar {{
        background-color: {COLORS["bg_secondary"]};
    }}
    QTabBar::tab {{
        background-color: {COLORS["bg_tab_inactive"]};
        color: {COLORS["text_secondary"]};
        padding: 8px 20px;
        margin-right: 2px;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        border: 1px solid {COLORS["border"]};
        border-bottom: none;
        min-width: 120px;
    }}
    QTabBar::tab:selected {{
        background-color: {COLORS["bg_tab_active"]};
        color: {COLORS["text_primary"]};
        font-weight: bold;
    }}
    QTabBar::tab:hover:!selected {{
        background-color: {COLORS["bg_hover"]};
    }}
    QTabBar::close-button {{
        image: none;
        subcontrol-position: right;
        padding: 2px;
    }}

    /* === Кнопки === */
    QPushButton {{
        background-color: {COLORS["bg_button"]};
        color: {COLORS["text_on_button"]};
        border: none;
        border-radius: 6px;
        padding: 8px 16px;
        font-weight: bold;
        font-size: {FONTS["size_normal"]};
    }}
    QPushButton:hover {{
        background-color: {COLORS["bg_button_hover"]};
    }}
    QPushButton:pressed {{
        background-color: {COLORS["accent"]};
    }}
    QPushButton:disabled {{
        background-color: {COLORS["bg_hover"]};
        color: {COLORS["text_secondary"]};
    }}
    QPushButton[danger="true"] {{
        background-color: {COLORS["bg_button_danger"]};
    }}

    /* === Поля ввода === */
    QLineEdit, QSpinBox {{
        background-color: {COLORS["bg_input"]};
        border: 1px solid {COLORS["border"]};
        border-radius: 6px;
        padding: 8px 12px;
        font-size: {FONTS["size_normal"]};
        color: {COLORS["text_primary"]};
    }}
    QLineEdit:focus, QSpinBox:focus {{
        border-color: {COLORS["border_focus"]};
    }}

    /* === Диалоги === */
    QDialog {{
        background-color: {COLORS["bg_primary"]};
    }}

    /* === Разделители === */
    QSplitter::handle {{
        background-color: {COLORS["border"]};
        width: 1px;
    }}
    QSplitter::handle:hover {{
        background-color: {COLORS["accent"]};
    }}

    /* === Полосы прокрутки === */
    QScrollBar:vertical {{
        background-color: {COLORS["scrollbar_bg"]};
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background-color: {COLORS["scrollbar_handle"]};
        border-radius: 5px;
        min-height: 30px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}

    /* === Статусбар === */
    QStatusBar {{
        background-color: {COLORS["bg_sidebar"]};
        border-top: 1px solid {COLORS["border"]};
        color: {COLORS["text_secondary"]};
        font-size: {FONTS["size_small"]};
    }}

    /* === Label === */
    QLabel {{
        color: {COLORS["text_primary"]};
    }}
    QLabel[secondary="true"] {{
        color: {COLORS["text_secondary"]};
        font-size: {FONTS["size_small"]};
    }}

    /* === GroupBox === */
    QGroupBox {{
        border: 1px solid {COLORS["border"]};
        border-radius: 8px;
        margin-top: 12px;
        padding-top: 16px;
        font-weight: bold;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        padding: 0 8px;
        color: {COLORS["text_secondary"]};
    }}

    /* === ComboBox === */
    QComboBox {{
        background-color: {COLORS["bg_input"]};
        border: 1px solid {COLORS["border"]};
        border-radius: 6px;
        padding: 6px 12px;
        min-width: 100px;
    }}
    QComboBox:focus {{
        border-color: {COLORS["border_focus"]};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    """
