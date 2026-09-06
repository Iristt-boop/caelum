"""订单：待确认单的状态机与存储（2026-09-06）。

设计见 `Caelum-点单确认卡-设计.md`。一句话：
**下单这件事从「模型说了算」变成「她点了算」。**

模型调 `luckin_order` 时工具**不下单**，只出一张确认卡；
真正的 `createOrder` 挪到 `/api/nox/orders/{id}/confirm` 后面 ——
模型物理上够不到它，因为它不在工具表里。

这是把「确认制」从一句提示词变成代码。在这之前，
`tools/mcd.py` 的 `ACTION_TOOLS` 常量定义完之后**整个仓库没有任何地方用到**，
测试也只断言「描述里有『确认』二字」—— 验的是那句话写了没有，
不是确认真的发生了没有。
"""

from orders.store import (  # noqa: F401
    CANCELLED, CONFIRMED, EXPIRED, FINAL, ORDER_FAILED, PAID,
    PAYMENT_UNKNOWN, PENDING, PENDING_PAYMENT, TTL, OrderStore,
)

__all__ = [
    "OrderStore", "TTL", "FINAL",
    "PENDING", "CONFIRMED", "PENDING_PAYMENT", "PAID",
    "EXPIRED", "CANCELLED", "ORDER_FAILED", "PAYMENT_UNKNOWN",
]
