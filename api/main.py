"""
Vercel ASGI 入口 — 组装 FastAPI 应用。
Vercel 将 /api/* 请求转发到此文件，冷启动时加载。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.models.database import init_db, load_simulated_time
from api.routes.group import router as group_router
from api.routes.tasks import router as task_router
from api.routes.rewards import router as reward_router
from api.routes.logs import router as logs_router
from api.routes.children import router as children_router
from api.routes.admin import router as admin_router
from api.routes.loans import router as loan_router

app = FastAPI(title="儿童积分系统")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(group_router)
app.include_router(task_router)
app.include_router(reward_router)
app.include_router(logs_router)
app.include_router(children_router)
app.include_router(admin_router)
app.include_router(loan_router)


try:
    init_db()
    load_simulated_time()
except Exception:
    import traceback
    traceback.print_exc()
