# -*- coding: utf-8 -*-
"""Шифрование паролей.

Используем Fernet (AES-128-CBC) для шифрования SSH-паролей.
Ключ шифрования генерируется из мастер-пароля через PBKDF2.
Соль хранится в отдельном файле.
"""

from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from src.core.config import config


class CryptoManager:
    """Управление шифрованием паролей.

    Attributes:
        _fernet: Экземпляр Fernet для шифрования/дешифрования.
        _salt_path: Путь к файлу с солью.
    """

    def __init__(self) -> None:
        self._fernet: Fernet | None = None
        self._salt_path = config.salt_path

    @property
    def is_initialized(self) -> bool:
        """Проверка, инициализирован ли менеджер (есть ли сохранённая соль)."""
        return self._salt_path.exists()

    def _get_or_create_salt(self) -> bytes:
        """Получить соль из файла или создать новую."""
        if self._salt_path.exists():
            return self._salt_path.read_bytes()
        salt = os.urandom(16)
        self._salt_path.write_bytes(salt)
        return salt

    def _derive_key(self, password: str) -> bytes:
        """Вывести ключ шифрования из мастер-пароля через PBKDF2.

        Args:
            password: Мастер-пароль пользователя.

        Returns:
            Ключ в формате base64 для Fernet.
        """
        salt = self._get_or_create_salt()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=600_000,  # OWASP рекомендация
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
        return key

    def unlock(self, password: str) -> bool:
        """Разблокировать хранилище мастер-паролем.

        При первом запуске создаёт проверочный токен.
        При последующих - проверяет мастер-пароль.

        Args:
            password: Мастер-пароль.

        Returns:
            True если пароль верный, False иначе.
        """
        key = self._derive_key(password)
        self._fernet = Fernet(key)

        verify_path = config.config_dir / "verify"
        if not verify_path.exists():
            # Первый запуск - создаём проверочный токен
            token = self._fernet.encrypt(b"ssh-commander-verify")
            verify_path.write_bytes(token)
            return True

        # Проверяем пароль дешифровкой проверочного токена
        try:
            data = self._fernet.decrypt(verify_path.read_bytes())
            return data == b"ssh-commander-verify"
        except InvalidToken:
            self._fernet = None
            return False

    def encrypt(self, plaintext: str) -> str:
        """Зашифровать строку.

        Args:
            plaintext: Открытый текст.

        Returns:
            Зашифрованная строка в base64.

        Raises:
            RuntimeError: Если хранилище не разблокировано.
        """
        if self._fernet is None:
            raise RuntimeError("Хранилище не разблокировано. Вызовите unlock() сначала.")
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """Расшифровать строку.

        Args:
            ciphertext: Зашифрованная строка в base64.

        Returns:
            Открытый текст.

        Raises:
            RuntimeError: Если хранилище не разблокировано.
            InvalidToken: Если данные повреждены или пароль неверный.
        """
        if self._fernet is None:
            raise RuntimeError("Хранилище не разблокировано. Вызовите unlock() сначала.")
        data = self._fernet.decrypt(ciphertext.encode("utf-8"))
        return data.decode("utf-8")

    def change_password(
        self, old_password: str, new_password: str,
    ) -> tuple[bool, str]:
        """Сменить мастер-пароль.

        Перегенерирует соль и ключ шифрования.
        Возвращает (success, old_fernet) для перешифровки паролей в БД.

        Args:
            old_password: Текущий мастер-пароль.
            new_password: Новый мастер-пароль.

        Returns:
            (True, "") при успехе, (False, "сообщение об ошибке") при неудаче.
        """
        # Проверяем текущий пароль
        old_key = self._derive_key(old_password)
        old_fernet = Fernet(old_key)

        verify_path = config.config_dir / "verify"
        try:
            data = old_fernet.decrypt(verify_path.read_bytes())
            if data != b"ssh-commander-verify":
                return False, "Неверный текущий пароль."
        except InvalidToken:
            return False, "Неверный текущий пароль."

        # Генерируем новую соль
        new_salt = os.urandom(16)
        self._salt_path.write_bytes(new_salt)

        # Генерируем новый ключ
        new_key = self._derive_key(new_password)
        new_fernet = Fernet(new_key)

        # Перешифровываем verify-токен
        new_token = new_fernet.encrypt(b"ssh-commander-verify")
        verify_path.write_bytes(new_token)

        # Обновляем текущий fernet
        self._fernet = new_fernet
        self._old_fernet = old_fernet

        return True, ""

    def reencrypt(self, ciphertext: str) -> str:
        """Перешифровать строку старым ключом -> новым ключом.

        Используется при смене мастер-пароля.

        Args:
            ciphertext: Строка, зашифрованная старым ключом.

        Returns:
            Строка, зашифрованная новым ключом.
        """
        if not hasattr(self, "_old_fernet") or self._old_fernet is None:
            raise RuntimeError("Нет старого ключа для перешифровки.")
        if self._fernet is None:
            raise RuntimeError("Хранилище не разблокировано.")

        plaintext = self._old_fernet.decrypt(ciphertext.encode("utf-8"))
        new_token = self._fernet.encrypt(plaintext)
        return new_token.decode("utf-8")


# Глобальный экземпляр
crypto = CryptoManager()
