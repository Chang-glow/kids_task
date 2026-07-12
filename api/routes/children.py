"""孩子管理路由."""

from fastapi import APIRouter, HTTPException, Depends
from api.dependencies import get_group_id
from api.models.database import get_db
from api.models.schemas import AddChildRequest
from api.config import now_cst

router = APIRouter(prefix="/api/children", tags=["children"])


@router.post("")
def add_child(req: AddChildRequest, group_id: int = Depends(get_group_id)):
    """在群组中添加孩子"""
    if len(req.name.strip()) == 0:
        raise HTTPException(status_code=400, detail="孩子名称不能为空")
    conn = get_db()
    cur = conn.cursor()
    now = now_cst()
    cur.execute(
        "INSERT INTO children (group_id, name, emoji, created_at) VALUES (%s, %s, %s, %s) RETURNING id",
        (group_id, req.name.strip(), req.emoji, now),
    )
    child_id = cur.fetchone()["id"]
    conn.commit()
    cur.execute("SELECT * FROM children WHERE id = %s", (child_id,))
    child = cur.fetchone()
    conn.close()
    return dict(child)
