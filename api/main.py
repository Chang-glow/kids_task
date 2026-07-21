"""
FastAPI 入口 — 组装应用，本地开发 + Vercel 部署共用。
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

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

# 静态文件（开发环境）
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(static_dir):
    @app.get("/")
    def serve_frontend():
        return FileResponse(os.path.join(static_dir, "index.html"))

    @app.get("/admin")
    def serve_admin():
        return FileResponse(os.path.join(static_dir, "admin.html"))


try:
    init_db()
    load_simulated_time()
except Exception:
    import traceback
    traceback.print_exc()
