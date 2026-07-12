"""FastAPI 依赖注入：从 X-Group-Code header 解析 group_id。"""

from fastapi import Header, HTTPException
from api.models.database import get_db


def get_group_id(x_group_code: str = Header(..., alias="X-Group-Code")) -> int:
    """从请求头 X-Group-Code 读取 invite_code 并解析为 group_id。"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM family_groups WHERE invite_code = %s", (x_group_code,))
    group = cur.fetchone()
    conn.close()
    if not group:
        raise HTTPException(status_code=404, detail="群组不存在")
    return group["id"]
