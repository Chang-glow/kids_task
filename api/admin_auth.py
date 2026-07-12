"""Admin 认证：PBKDF2 密码哈希 + 内存 token 管理。"""

import hashlib
import os
import secrets
import time

# 内存 token 存储（重启失效）
_active_tokens: dict[str, float] = {}  # token → expiry_timestamp

TOKEN_TTL = 3600 * 8  # 8 小时
PBKDF2_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 哈希密码，格式 salt_hex:key_hex"""
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS, dklen=32)
    return salt.hex() + ":" + key.hex()


def verify_password(password: str, stored_hash: str) -> bool:
    """验证密码。兼容旧版纯 SHA256 哈希（无冒号分隔）。"""
    if ":" not in stored_hash:
        # 旧版纯 SHA256 — 一次性校验后自动升级
        return hashlib.sha256(password.encode()).hexdigest() == stored_hash
    salt_hex, key_hex = stored_hash.split(":")
    salt = bytes.fromhex(salt_hex)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS, dklen=32)
    return key.hex() == key_hex


def generate_token() -> str:
    """生成随机 token 并存入内存，返回 token 字符串"""
    token = secrets.token_hex(32)
    _active_tokens[token] = time.time() + TOKEN_TTL
    return token


def validate_token(token: str) -> bool:
    """验证 token 是否有效（存在且未过期）"""
    if token not in _active_tokens:
        return False
    if time.time() > _active_tokens[token]:
        del _active_tokens[token]
        return False
    return True


def invalidate_token(token: str) -> None:
    _active_tokens.pop(token, None)
