"""Hook 注册器 — 观察者模式，同步执行，异常隔离。"""

from typing import Any, Callable

_registry: dict[str, list[Callable]] = {}


def register(event: str, handler: Callable) -> None:
    """注册 hook handler。handler 接收 **kwargs，异常不传播。"""
    _registry.setdefault(event, []).append(handler)


def emit(event: str, **kwargs) -> list[Any]:
    """触发 hook。所有 handler 按注册顺序同步执行，单个异常不中断其他。"""
    results = []
    for h in _registry.get(event, []):
        try:
            results.append(h(**kwargs))
        except Exception:
            pass
    return results


def unregister(event: str, handler: Callable) -> None:
    """取消注册。"""
    try:
        _registry[event].remove(handler)
    except (KeyError, ValueError):
        pass


def clear_event(event: str) -> None:
    """清空某个事件的所有 handler（测试用）。"""
    _registry.pop(event, None)
