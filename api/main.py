"""
FastAPI 入口 — 组装应用，本地开发 + Vercel 部署共用。
"""

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from api.models.database import init_db, load_simulated_time
from api.hooks.builtins import register_all as register_builtin_hooks
from api.hooks.investment_hooks import register_all as register_investment_hooks
from api.routes.group import router as group_router
from api.routes.tasks import router as task_router
from api.routes.rewards import router as reward_router
from api.routes.logs import router as logs_router
from api.routes.children import router as children_router
from api.routes.admin import router as admin_router
from api.routes.loans import router as loan_router
from api.routes.medals import router as medal_router
from api.routes.investments import router as investment_router

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
app.include_router(medal_router)
app.include_router(investment_router)

# 静态文件（开发环境）
root_dir = os.path.join(os.path.dirname(__file__), "..")
if os.path.isfile(os.path.join(root_dir, "index.html")):
    @app.get("/")
    def serve_frontend():
        return FileResponse(os.path.join(root_dir, "index.html"))

    @app.get("/admin")
    def serve_admin():
        return FileResponse(os.path.join(root_dir, "admin.html"))

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        file_path = os.path.realpath(os.path.join(root_dir, full_path))
        root_real = os.path.realpath(root_dir)
        if not file_path.startswith(root_real + os.sep) and file_path != root_real:
            raise HTTPException(status_code=404)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(root_dir, "index.html"))


try:
    register_builtin_hooks()
    register_investment_hooks()
    init_db()
    load_simulated_time()
except Exception:
    import traceback
    traceback.print_exc()
