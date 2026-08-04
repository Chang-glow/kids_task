"""Admin 认证：PBKDF2 密码哈希 + JWT 无状态 token 管理 + DB 撤销表。"""

import hashlib
import os
import time
import secrets
from datetime import datetime, timezone

import jwt

from api.config import JWT_SECRET
from api.models.database import get_db

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
    """生成 JWT token，payload 含 jti（唯一 ID）和 exp（过期时间）。"""
    now = int(time.time())
    payload = {
        "jti": secrets.token_hex(16),
        "iat": now,
        "exp": now + TOKEN_TTL,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _decode(token: str) -> dict | None:
    """解码 JWT，返回 payload 或 None。不比对待撤销表（由调用方判断）。"""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def _is_revoked(jti: str) -> bool:
    """检查 jti 是否在撤销表中。

    异常策略：
    - 表不存在（init_db 未完成）→ 放行（fail open），否则 admin 完全不可用
    - 连接失败等其它错误 → 拒绝（fail closed），宁严勿松
    """
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM revoked_tokens WHERE jti = %s", (jti,))
        result = cur.fetchone() is not None
        conn.close()
        return result
    except Exception as e:
        # 表不存在 = init_db 没跑完，不是安全威胁，放行
        if hasattr(e, 'pgcode') and getattr(e, 'pgcode', None) == '42P01':
            return False
        # UndefinedTable 也可能不以 pgcode 形式暴露，检查消息
        msg = str(e)
        if 'relation' in msg and 'does not exist' in msg:
            return False
        return True  # 真正的 DB 不可达，宁严勿松


def _cleanup_expired_revocations() -> None:
    """清理已过期的撤销条目（token 本身已过期，撤销记录无保留价值）。"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM revoked_tokens WHERE expires_at < %s", (datetime.now(timezone.utc),))
        conn.commit()
        conn.close()
    except Exception:
        pass


def validate_token(token: str) -> bool:
    """验证 JWT token 签名、过期时间、以及是否已被撤销。"""
    payload = _decode(token)
    if payload is None:
        return False
    if _is_revoked(payload["jti"]):
        return False
    _cleanup_expired_revocations()
    return True


def invalidate_token(token: str) -> None:
    """将 token 的 jti 写入撤销表，实现服务端主动失效。
    使用 JWT 的 exp 作为撤销条目过期时间——token 过期后撤销记录自动清理。"""
    payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"],
                         options={"verify_exp": False})
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO revoked_tokens (jti, expires_at) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (payload["jti"], datetime.fromtimestamp(payload["exp"], tz=timezone.utc)),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
