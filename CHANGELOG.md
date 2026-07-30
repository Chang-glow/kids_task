# Changelog

> 记录每次修改的动机和影响范围，便于回溯"什么时候改了什么、为什么"。

---

## 2026-07-30

### fix: 悬赏刷新假调用修复

**问题**：刷新悬赏提示 `this.loadChildren is not a function`，消耗刷新次数但不刷新 UI。  
**原因**：`refreshOneCondition` 调用了不存在的方法 `loadChildren()`，应为 `loadGroupInfo()`。  
**影响**：`index.html` L1684。

### fix: 异步错误处理加固

- 7 个 `catch (e) { /* ignore */ }` / `catch (_) {}` → `console.error('方法名 failed', e)`，错误不再静默丢弃
- `refreshOneCondition` 写操作与 UI 刷新分离：API 写失败 early return，避免"后端已成功但前端提示失败"
- 4 个关键加载函数（loadGroupInfo / loadTasks / loadRewards / loadLogs）的 toast 附加后端错误原文，便于排查

### feat: 优惠券折扣改为斐波那契累进

**旧规则**：每章 2% 线性叠加，5 章 = 10%，10 章 = 20%。  
**新规则**：5 章保底 10%，第 6 章起按 Fibonacci(+3, +5, +8, +13, +21...) 递增。

| 章数 | 5 | 6 | 7 | 8 | 9 | 10 | 12 |
|------|---|---|---|---|---|---|---|----|
| 折扣 | 10% | 13% | 18% | 26% | 39% | 60% | 149% |

**影响文件**：`api/services/medal_service.py`、`api/routes/rewards.py`、`index.html`、`tests/test_medals.py`

### docs: 前端拆分解耦分析

新增 `FRONTEND_SPLIT.md`，记录 HTML/CSS/JS 拆分的收益与风险，暂不实施。
