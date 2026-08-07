# 部署指南

多种部署方案，根据需求选择。

## 方案一：Vercel + Supabase（零成本起步）

**适合：** 不想管服务器，免费额度够小家庭用。

### 后端部署到 Vercel

项目根目录已有 `vercel.json`，直接导入 Vercel 即可：

```bash
vercel deploy
```

### 数据库：Supabase PostgreSQL

1. 在 [Supabase](https://supabase.com) 创建项目
2. 获取 **session pooler** 连接字符串（`aws-*.pooler.supabase.com:5432`），这是唯一支持 IPv4 的免费 pooler 模式
3. 在 Vercel 项目设置中添加环境变量 `DATABASE_URL`

### 环境变量

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` | PostgreSQL 连接字符串 |
| `ADMIN_JWT_SECRET` | Admin JWT 签名密钥（不设则自动生成） |

### 已知坑点

- Vercel 免费层仅 IPv4，Supabase transaction pooler 不支持 session 模式，必须用 session pooler
- 国内访问 Vercel 域名可能不稳定，可自备域名 CNAME 到优选 IP
- Vercel Serverless 有 10s 超时限制，复杂查询可能超时

---

## 方案二：自建 VPS

**适合：** 国内访问、完全掌控、有 Linux 基础。

### 后端

FastAPI 应用部署到任意 VPS：

```bash
# 直接运行
bash run.sh

# 或用 systemd 管理
# 或用 Docker
docker build -t kids-task .
docker run -d -p 8001:8001 --env-file .env kids-task
```

### 数据库

VPS 上安装 PostgreSQL，或继续用 Supabase。

### 反向代理

Nginx/Caddy 反代到 8001 端口，配上 HTTPS。

---

## 方案三：Cloudflare Tunnel

**适合：** 家里有闲置电脑/NAS，不想买服务器。

1. 在家里的机器上运行 `run.sh`
2. 安装 [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)（`cloudflared`）
3. 配置 tunnel 指向 `localhost:8001`

数据库仍可连 Supabase 或本地 PG。

---

## 方案四：GitHub Pages + 后端分开部署

**适合：** 前端想放静态托管，后端独立部署。

- 前端（`index.html` + `admin.html`）部署到 GitHub Pages / Cloudflare Pages
- 后端（`api/`）部署到 VPS 或 Vercel
- 注意 CORS 配置，前端需指向后端 API 地址

---

## 数据库初始化

无论哪种方案，首次启动时应用会自动建表（`CREATE TABLE IF NOT EXISTS`）。无需手动跑 SQL。

## 北京时间

系统所有时间戳使用 UTC+8。代码中 `now_cst()` 统一获取北京时间。
