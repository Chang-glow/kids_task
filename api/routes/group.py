"""群组路由：创建、查询。"""

import uuid

from fastapi import APIRouter, HTTPException
from api.models.database import get_db
from api.models.schemas import CreateGroupRequest
from api.config import now_cst

router = APIRouter(prefix="/api/groups", tags=["groups"])


def _generate_invite_code() -> str:
    """生成 16 位邀请码（64 bits 熵），方便 URL 分享。"""
    return uuid.uuid4().hex[:16]


@router.post("")
def create_group(req: CreateGroupRequest):
    """创建新群组 + 默认孩子，返回 invite_code。"""
    conn = get_db()
    cur = conn.cursor()
    now = now_cst()
    invite_code = _generate_invite_code()

    cur.execute(
        "INSERT INTO family_groups (name, invite_code, created_at) VALUES (%s, %s, %s) RETURNING id",
        (req.name, invite_code, now),
    )
    group = cur.fetchone()

    cur.execute(
        "INSERT INTO children (group_id, name, emoji, created_at) VALUES (%s, %s, %s, %s) RETURNING id",
        (group["id"], req.child_name, "👶", now),
    )
    child = cur.fetchone()

    conn.commit()
    conn.close()

    return {
        "invite_code": invite_code,
        "name": req.name,
        "children": [
            {"name": req.child_name, "emoji": "👶", "total_points": 0}
        ],
    }


@router.get("/{invite_code}")
def get_group(invite_code: str):
    """通过邀请码获取群组信息 + 孩子列表。"""
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT id, name, invite_code, created_at FROM family_groups WHERE invite_code = %s",
        (invite_code,),
    )
    group = cur.fetchone()
    if not group:
        conn.close()
        raise HTTPException(status_code=404, detail="群组不存在")

    cur.execute(
        "SELECT id, name, emoji, total_points FROM children WHERE group_id = %s ORDER BY id",
        (group["id"],),
    )
    children = [dict(c) for c in cur.fetchall()]
    conn.close()

    return {
        "id": group["id"],
        "name": group["name"],
        "invite_code": group["invite_code"],
        "created_at": group["created_at"].isoformat() if group["created_at"] else None,
        "children": children,
    }
