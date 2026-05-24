.PHONY: run build install uninstall clean lint test

VENV = .venv
PYTHON = $(VENV)/bin/python
PIP = $(VENV)/bin/pip
APP_NAME = ssh-commander
INSTALL_DIR = $(HOME)/.local/bin
DESKTOP_DIR = $(HOME)/.local/share/applications
ICON_DIR = $(HOME)/.local/share/icons

# Запуск из исходников
run:
	$(PYTHON) -m src.main

# Сборка бинарника
build:
	$(VENV)/bin/pyinstaller cmd.spec

# Установка как системное приложение
install: build
	mkdir -p $(INSTALL_DIR)
	cp dist/$(APP_NAME) $(INSTALL_DIR)/
	mkdir -p $(DESKTOP_DIR)
	cp resources/ssh-commander.desktop $(DESKTOP_DIR)/
	mkdir -p $(ICON_DIR)
	@if [ -f resources/icons/app.png ]; then \
		cp resources/icons/app.png $(ICON_DIR)/ssh-commander.png; \
	fi
	@echo "Установлено. Приложение доступно в меню Ubuntu."

# Удаление
uninstall:
	rm -f $(INSTALL_DIR)/$(APP_NAME)
	rm -f $(DESKTOP_DIR)/ssh-commander.desktop
	rm -f $(ICON_DIR)/ssh-commander.png
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
