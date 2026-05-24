# SSH Commander

Десктопное приложение для Ubuntu для управления SSH-соединениями.

## Возможности

- Сохранение SSH-подключений (хост, порт, логин, пароль)
- Подключение одним кликом
- Несколько соединений во вкладках
- Избранные команды с быстрым запуском
- Копирование файлов между серверами (SFTP)

## Установка и запуск

### Из исходников (для разработки)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make run
```

### Сборка и установка

```bash
pip install -r requirements.dev.txt
make build     # сборка бинарника
make install   # установка в систему
```

## Зависимости

- Python 3.10+
- PySide6 (GUI)
- paramiko (SSH)
- pyte (эмуляция терминала)
- cryptography (шифрование паролей)
