"""bridge 的 REST 客户端。

相册、待办、日记的数据都在 bridge 的 SQLite 里。Core 是独立进程，
**不能直接读那个 db 文件** —— 两个进程同时写 SQLite 会锁表甚至损坏，
而 bridge 正在高频写（每轮对话都落库）。所以走它的 HTTP 接口。

鉴权用 X-Nox-Token，跟前端走同一套。token 从环境变量读，
部署时从 bridge 的 systemd Environment 取，不写进代码。
"""

from __future__ import annotations

from tools.http import RestClient, RestResult

# 老名字，外面还在用
BridgeResult = RestResult


class BridgeClient(RestClient):
    def __init__(self, base_url: str, token: str = "", timeout: float = 10.0) -> None:
        super().__init__(
            base=base_url,
            headers={"X-Nox-Token": token} if token else {},
            timeout=timeout,
            auth_hint="X-Nox-Token 没配或不对",
        )

    def ping(self) -> RestResult:
        """启动自检。别等第一次用工具才发现 token 不对。

        走 /api/auth/verify 而不是 /health —— 鉴权中间件只挂在 /api 上，
        打 /health 连不上 token 对不对都不知道，等于白探。
        """
        return self.get("/api/auth/verify")
