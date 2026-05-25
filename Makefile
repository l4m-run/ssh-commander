.PHONY: run run-noauth reset build install uninstall clean lint test

VENV = .venv
PYTHON = $(VENV)/bin/python
PIP = $(VENV)/bin/pip
APP_NAME = opsdesk
INSTALL_DIR = $(HOME)/.local/bin
DESKTOP_DIR = $(HOME)/.local/share/applications
ICON_DIR = $(HOME)/.local/share/icons

# Запуск из исходников
run:
	$(PYTHON) -m src.main

# Запуск без мастер-пароля
run-noauth:
	$(PYTHON) -m src.main --no-auth

# Сброс всех данных (подключения, ключи)
reset:
	$(PYTHON) -m src.main --reset

# Сборка бинарника
build:
	$(VENV)/bin/pyinstaller cmd.spec

# Установка как системное приложение
install: build
	mkdir -p $(INSTALL_DIR)
	cp dist/$(APP_NAME) $(INSTALL_DIR)/
	mkdir -p $(DESKTOP_DIR)
	cp resources/opsdesk.desktop $(DESKTOP_DIR)/
	mkdir -p $(ICON_DIR)
	@if [ -f resources/icons/app.png ]; then \
		cp resources/icons/app.png $(ICON_DIR)/opsdesk.png; \
	fi
	@echo "Установлено. Приложение доступно в меню Ubuntu."

# Удаление
uninstall:
	rm -f $(INSTALL_DIR)/$(APP_NAME)
	rm -f $(DESKTOP_DIR)/opsdesk.desktop
	rm -f $(ICON_DIR)/opsdesk.png
	@echo "Удалено."

# Линтер
lint:
	$(VENV)/bin/ruff check src/

# Тесты
test:
	$(VENV)/bin/pytest tests/ -v

# Очистка артефактов сборки
clean:
	rm -rf dist/ build/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
