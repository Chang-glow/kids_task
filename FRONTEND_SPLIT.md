# 前端拆分解耦方案

> 只记录，不实施。本文档是对 HTML/CSS/JS 分文件部署的完整分析。
> 讨论日期: 2026-07-30

---

## 零、当前状态

| 文件 | 总行数 | CSS (inline) | HTML body | JS (inline) |
|------|--------|-------------|-----------|-------------|
| `index.html` | 2281 | ~700 行 (L10-711) | ~740 行 | ~830 行 (L1450-2279) |
| `admin.html` | 1585 | ~75 行 (L8-81) | ~695 行 | ~805 行 (L777-1583) |

后端 `api/main.py:50-58` 已有 `/{full_path:path}` catch-all 路由，可直接响应根目录下任意静态文件。Vercel 上因 `vercel.json` 只 rewrite `/api/*`，根目录文件由 edge CDN 直出，不走 function。

---

## 一、目标结构

```
kids_task/
├── index.html          # <link rel="stylesheet" href="/style.css">
│                       # <script defer src="/app.js"></script>
├── admin.html          # <link rel="stylesheet" href="/admin.css">
│                       # <script defer src="/admin.js"></script>
├── style.css           # index.html 的 CSS
├── app.js              # index.html 的 JS（Alpine.js app 定义）
├── admin.css           # admin.html 的 CSS
└── admin.js            # admin.html 的 JS
```

### 1.1 HTML 改动

每文件改两处，各一行：

```html
<!-- 替换 <style>...</style> -->
<link rel="stylesheet" href="/style.css">

<!-- 替换 <script>...</script>（放在原位置即 </body> 前） -->
<script defer src="/app.js"></script>
```

`defer` 保证 JS 在 DOM 解析完成后执行，与当前 inline 脚本行为一致。

### 1.2 CSS 公共部分（可选，额外工作）

`admin.html` 的 75 行 CSS 与 `index.html` 前 75 行有大量重复（颜色变量、字体、按钮、toast、modal 等）。拆分后可进一步提取公共文件：

```
style.css        # 共享基础：变量、字体、按钮、toast、modal
index.css        # 首页特有：任务卡片、进度条、FAB
admin.css        # 管理页特有：表格、表单
```

**注意**：这需要手动识别公共部分，比单纯切分多出额外工作量。

---

## 二、收益

### 2.1 编辑器错误检测

inline JS 在 HTML 文件中不会触发语言服务的语义分析。像 `this.loadChildren()` 这种调用不存在方法的 bug，如果 `app.js` 是独立 `.js` 文件，VS Code 的 JS/TS 语言服务会直接标黄线 "method not found"。这是 `loadChildren` bug 存活的直接原因。

### 2.2 网络缓存粒度

| 场景 | 内联（现状） | 拆分后 |
|------|------------|--------|
| 改 1 行 JS | 90KB 缓存全部失效 | 只重新下载 18KB 的 app.js |
| 改 1 行 CSS | 90KB 全部失效 | 只重新下载 12KB 的 style.css |
| 只改 HTML | 90KB 全部失效 | 只重新下载 20KB 的 index.html |
| CSS 没改 | 每次重新下载 | 304 Not Modified（0KB 传输） |

### 2.3 Vercel 计费

`vercel.json` 只 rewrite `/api/*` 到 Python function。拆分出来的 `style.css`、`app.js` 等文件被 Vercel 自动识别为静态资源 — 不触发 function 调用，不计入执行配额。当前 `/` 首页本身仍需 `FileResponse` 返回，但 CSS/JS 的请求量可以省掉。

### 2.4 代码 review

diff 从 `index.html +150 -200` 变成 `app.js +30 -5`，review 者能立即知道改了什么层面。

---

## 三、风险

### 3.1 浏览器缓存过期（最高风险 ⚠️）

内联方案的"版本号"就是 HTML 自身 — HTML 变了浏览器自然不用缓存。拆分后 `app.js` 文件名不变，浏览器可能长期缓存旧版本，导致 JS 改了对用户不可见。

**缓解方案**（需引入构建步骤）：

- **方案 A**：`run.sh` 中用 `sed` 将 `app.js?v=<git-hash-8>` 注入 HTML 的 `<script src>` 属性，每次部署自动更新版本号
- **方案 B**：Vercel 的 `vercel.json` 配 `headers` 给 `.js` / `.css` 设短缓存（如 `max-age=300`），缺点是性能打折
- **方案 C**：保持当前零构建部署，接受手动改版本号（人工漏改风险）

**核心矛盾**：项目目前是零构建步骤的极简部署。拆分需要引入一个最小的构建环节。

### 3.2 加载时序

外部 `<script src>` 不带 `defer` 放在 `</body>` 前，行为与当前 inline 脚本完全一致（同步阻塞执行，DOM 已就绪）。加 `defer` 放到 `<head>` 也行，但要确保 Alpine.js 的 `x-init="init()"` 在脚本执行前不触发 — Alpine `defer` + 用户脚本 `defer` 按声明顺序执行，安全。

**无实际风险**，只要别把 `<script src>` 放到 `<head>` 且忘写 `defer`。

### 3.3 全局变量

当前 JS 在全局作用域定义 `STAR_MULTS`、`STAR_INFO` 常量和 `app()` 函数。拆分到外部 `.js` 文件后仍在全局作用域，行为无变化。

### 3.4 额外 HTTP 请求

从 1 个请求变成 3 个（HTML + CSS + JS）。本地 WSL 环境下可忽略（localhost 延迟 < 1ms）。Vercel 上走 HTTP/2 多路复用 + edge CDN，实际影响微小。

---

## 四、实施步骤（若决定执行）

1. 新建 `style.css`（从 index.html L10-711 提取）
2. 新建 `app.js`（从 index.html L1450-2278 提取，去除 `<script>` 包裹）
3. 新建 `admin.css`（从 admin.html L8-81 提取）
4. 新建 `admin.js`（从 admin.html L777-1583 提取）
5. 修改 `index.html`：`<style>` → `<link>`，inline `<script>` → `<script src>`
6. 修改 `admin.html`：同上
7. 修改 `run.sh`：加入版本号替换逻辑（`git rev-parse --short HEAD` → cache-bust query string）
8. 端到端测试：创建群组、任务完成、悬赏刷新、奖励兑换、管理后台

---

## 五、不做的理由

- 单人项目，CSS ~700 行 / JS ~830 行在单文件内尚可管理
- 当前无构建步骤，拆分引入的缓存问题需要版本号机制 — 这是拆分本身不会解决的问题，而是拆完之后多出来的新问题
- 如果后续引入 Vite/Webpack 等构建工具，拆分是自然结果，届时一步到位
- `HOOKS.md` 记录的 hooks 系统才是更影响架构的长期改进，前端拆分属于工程卫生层面的优化
