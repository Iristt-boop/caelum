"""本地执行端的链路 —— 他伸向她电脑的那只手。

见 `D:\\claude-code\\CAELUM-HARNESS-ARCHITECTURE.md`。一句话：

    心 = Nox Core（这里，VPS）  手 = Caelum Harness（她的 PC）

## 🔴 方向和别的 MCP 都不一样

`McpClient` 那几个（Ombre Brain / ha-mcp / health-mcp）都是**我们去连它们**。
这一条是反的：

    TCP 方向      PC ──────▶ VPS      她的电脑主动连上来
    MCP 角色      Nox = client        Gateway = server

为什么这么设计：她家里是 NAT + 动态 IP，反过来要开公网端口、穿透、
配动态 DNS，**而且给她的电脑开了一个入站攻击面**。主动外连之后
那些问题一次性消失。

代价是这边不能复用 `McpClient` —— 那个假设"我发起连接"。
所以这里自己维护一条常驻链路，但**对外暴露同样的接口**
（`call` / `acall` / `list_tools` / `CallResult`），
上层用起来和别的 MCP 没区别。

## 一条硬规矩：不抛异常

和 `mcp_client.py` 一样。她的电脑关机了、网断了、握手没过 ——
都返回 `ok=False`，绝不让对话崩。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from tools.mcp_client import CallResult

logger = logging.getLogger(__name__)

#: 密钥最短长度。**必须和 TS 那边的 MIN_SECRET_LENGTH 一致**
MIN_SECRET_LENGTH = 32

#: 一次调用等多久。比 HTTP 那几个短 —— 本地执行要么很快，
#: 要么就是卡住了，让她干等三十秒没有意义
CALL_TIMEOUT_S = 20.0

#: 🔴 **要她点头的操作等多久。**
#:
#: 弹窗给她 60 秒（`ApprovalDialog.jsx` 的 `TIMEOUT_S`）。
#: 这个数必须**大于**那个，否则她还没来得及看清路径，
#: 这边就已经超时了 —— 那等于审批功能根本不存在。
#:
#: 2026-08-25 实测栽在这儿：弹窗正常弹出，她那边一切正常，
#: 但 Nox 20 秒就报「超时」，三次错误三个样子，
#: 他自己得出的结论是「写链路本身不稳定」—— 完全找错了方向。
#:
#: 15 秒富余是留给：帧在公网上的往返 + 她点完之后真正执行的时间。
APPROVAL_TIMEOUT_S = 75.0

#: 握手宽限。她的电脑连上来之后这么久还没证明身份就踢掉
HANDSHAKE_TIMEOUT_S = 10.0


def _sign(secret: str, parts: list[str]) -> str:
    """HMAC 签名。

    🔴 **这个函数必须和 TS 那边逐字节一致**
    （`packages/caelum/local-gateway/src/auth.ts` 的 `sign`）。
    差一个字节，握手就永远失败，而报错只会说"身份证明不对"。

    ⚠️ 长度用的是 **UTF-8 字节数**，不是 `len()`。
    JS 的 `.length` 数 UTF-16 码元、Python 的 `len()` 数字符 ——
    同一个 `pc-🖥`，JS 说 5、Python 说 4。设备名里有个 emoji
    就会两边对不上，而且极难查。
    """
    payload = "|".join(f"{len(p.encode('utf-8'))}:{p}" for p in parts)
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def read_secret(env: dict[str, str] | None = None) -> str | None:
    """读密钥。

    🔴 **没配就返回 None，让调用方拒绝连接。**
    绝不能"没配就跳过认证" —— 那样一次配置疏忽等于把她的电脑
    敞开给任何连上来的人，而且**不会有任何报错**。
    """
    src = os.environ if env is None else env
    secret = src.get("CAELUM_LINK_SECRET")
    if not secret or len(secret) < MIN_SECRET_LENGTH:
        return None
    return secret


@dataclass
class _Pending:
    """一个发出去还没回来的请求。"""

    future: asyncio.Future[dict[str, Any]]


class LocalLink:
    """她电脑上那只手。

    ⚠️ **一次只保留一条连接。** 第二台设备连上来会顶掉前一台 ——
    多设备是 P2（架构文档第八节）。

    顶掉而不是拒绝，是因为 TCP 半开：她合上笔记本再打开时，
    旧连接可能还挂在这边没超时。拒绝新连接的话，
    她要等到旧连接超时才能重新连上 —— 那可能是几分钟。
    """

    def __init__(self, name: str = "computer", world: Any = None) -> None:
        self.name = name
        #: World Model。**可以为 None** —— 那样只是不记，链路照常
        self.world = world
        self._ws: Any = None
        self._device: str = ""
        self._next_id = 1
        self._pending: dict[int, _Pending] = {}
        #: WS 所属的事件循环。见 `call()` —— 同步接口要靠它把协程
        #: 投回正确的循环，不能自己新开一个
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------ 状态

    @property
    def is_ready(self) -> bool:
        """手现在连着吗。

        **他主动想干活之前要先看这个。** 直接调用再等超时的话，
        她会看到他愣了二十秒才说"连不上你电脑"。
        """
        return self._ws is not None

    @property
    def device_id(self) -> str:
        return self._device

    def describe(self) -> str:
        """一句人话，进上下文给他看。"""
        if not self.is_ready:
            return "她的电脑现在没连上"
        return f"她的电脑连着（{self._device}）"

    # ------------------------------------------------------------ 服务端：接受连接

    async def serve(self, ws: Any) -> None:
        """处理一条新连上来的链路。FastAPI 的 websocket 端点调这个。

        ⚠️ 整个函数**不抛** —— 它跑在 FastAPI 的连接处理里，
        抛出去只会在日志里留一条栈，而她那边只看到连接莫名断掉。
        """
        secret = read_secret()
        if secret is None:
            logger.error("没配 CAELUM_LINK_SECRET，拒绝所有本地链路连接")
            await _safe_close(ws)
            return

        try:
            device = await asyncio.wait_for(self._handshake(ws, secret), HANDSHAKE_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("本地链路握手超时")
            await _safe_close(ws)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("本地链路握手失败: %s", exc)
            await _safe_close(ws)
            return

        if device is None:
            await _safe_close(ws)
            return

        # 顶掉旧连接（见类注释）
        if self._ws is not None:
            logger.info("新设备 %s 连上，顶掉旧链路", device)
            await _safe_close(self._ws)

        self._ws = ws
        self._device = device
        #: 记下这条 WS 归哪个循环 —— 工具处理函数跑在别的线程里，
        #: 要靠它把请求投回来（见 `call()`）
        self._loop = asyncio.get_running_loop()
        logger.info("本地链路建立：%s", device)

        try:
            await self._read_loop(ws)
        except Exception as exc:  # noqa: BLE001
            logger.info("本地链路断开：%s", exc)
        finally:
            if self._ws is ws:
                self._ws = None
                self._device = ""
                self._loop = None
            # 🔴 链路断了要把等着的请求全部叫醒，否则它们会挂到超时。
            # 更要紧的是：调用方会以为"还在执行"，而其实那边已经没了
            self._fail_all("她的电脑断开了")

    async def _handshake(self, ws: Any, secret: str) -> str | None:
        """验 PC 的身份，并证明自己是 Nox Core。返回 deviceId，失败返回 None。"""
        raw = await ws.receive_text()
        msg = json.loads(raw)
        if msg.get("method") != "caelum/hello":
            logger.warning("第一帧不是 hello，拒绝")
            return None

        params = msg.get("params") or {}
        device = str(params.get("deviceId", ""))
        nonce = str(params.get("nonce", ""))
        proof = str(params.get("proof", ""))

        expect = _sign(secret, ["pc", device, nonce])
        # 常数时间比较 —— 用 == 会因为提前返回泄露"前几位对了"
        if not hmac.compare_digest(proof, expect):
            logger.warning("设备 %r 的身份证明不对，拒绝", device)
            return None

        # 回一份自己的证明。**带上她出的 nonce** —— 这样旧的回应重放不了
        my_nonce = secrets.token_hex(16)
        result = {
            "nonce": my_nonce,
            "proof": _sign(secret, ["vps", device, nonce, my_nonce]),
        }
        await ws.send_text(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": result}))
        return device

    async def _read_loop(self, ws: Any) -> None:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                # 坏帧不断链 —— 丢掉继续
                logger.warning("本地链路收到解析不了的帧")
                continue
            self._on_message(msg)

    def _on_message(self, msg: dict[str, Any]) -> None:
        mid = msg.get("id")
        if mid is None:
            #: 通知 —— 现在只有执行摘要（⑦）
            if msg.get("method") == "caelum/event":
                self._remember(msg.get("params") or {})
            return
        pending = self._pending.pop(int(mid), None)
        if pending is None:
            return  # 迟到的回应，对应的请求已经超时了
        if not pending.future.done():
            pending.future.set_result(msg)

    def _fail_all(self, why: str) -> None:
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(ConnectionError(why))
        self._pending.clear()

    def _remember(self, summary: dict[str, Any]) -> None:
        """把一次执行记进 World Model（⑧）。

        ## 🔴 为什么值得记

        这是「他在用电脑」和「他调了几个工具」的真正区别。

        一个月后她问「上次 CareLedger 为什么这么改」，
        他不该是重新读代码猜，而是
        「上次我们在处理 Care Ledger 不落库的问题，当时是因为……」。

        ## 记什么

        **只记事实，不记内容**：动了哪个文件、成没成、花了多久。
        摘要本身就已经不含内容了（`summary.ts` 那边把住的），
        这里不再往里加。

        ⚠️ **整个函数不抛** —— 记不下来不该影响链路。
        """
        if self.world is None:
            return
        try:
            capability = str(summary.get("capability") or "")
            if not capability:
                return
            paths = summary.get("paths") or []
            ok = bool(summary.get("ok"))

            #: dedup_key 用 at + capability + 路径 —— 同一次执行
            #  重复收到时不会记两遍（World Model 的幂等靠它）
            key = f"{summary.get('at')}|{capability}|{','.join(map(str, paths))}"
            self.world.observe(
                source="local_hand",
                type="他在电脑上做的事",
                observed={
                    "做了什么": capability,
                    "动了哪些": paths,
                    "成了吗": ok,
                    "设备": self._device,
                    **({"花了多久ms": summary["ms"]} if "ms" in summary else {}),
                    **({"没成的原因": summary["error"]} if summary.get("error") else {}),
                },
                observed_at=_parse_at(summary.get("at")),
                dedup_key=key,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("记录执行摘要失败（不影响链路）: %s", exc)

    # ------------------------------------------------------------ 客户端：发请求

    async def acall(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> CallResult:
        """调用她电脑上的一件能力。接口和 `McpClient.acall` 一致。

        @param timeout - 等多久。要她点头的操作得给 `APPROVAL_TIMEOUT_S`，
            不然弹窗还没到期这边就先超时了（见那个常量的注释）
        """
        return await self._request(
            "tools/call", {"name": tool, "arguments": args or {}}, timeout=timeout,
        )

    async def acall_method(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> CallResult:
        """直接打一个协议方法（不是 `tools/call`）。

        多步执行的 `caelum/work.start` / `caelum/work.end` 走这条 ——
        它们不是「用一件能力」，是「谈一次授权」。
        """
        return await self._request(method, params, timeout=timeout)

    def call_method(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> CallResult:
        """`acall_method` 的同步版。桥接方式和 `call` 完全一样。"""
        return self._sync(lambda: self.acall_method(method, params, timeout), timeout, method)

    def call(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> CallResult:
        """同步接口，给 AgentLoop 的工具处理函数用。

        ## 🔴 为什么不能用 `asyncio.run`

        别的工具（`McpClient` / `ob_client`）那么写是对的 —— 它们每次
        调用都自己开一条 HTTP 连接，用哪个循环都无所谓。

        **这里不一样：`self._ws` 是一条长连接，归 FastAPI 那个循环所有。**
        `asyncio.run` 会新开一个循环，在里面 `send_text` 等于跨循环碰
        另一个循环的 transport —— 轻则 `_pending` 里的 future 永远不 resolve
        （她看到他愣二十秒然后说超时），重则把整条链路搞坏，
        而她那边只看到「电脑断开了」。

        所以走 `run_coroutine_threadsafe`：协程回到 WS 自己的循环里执行，
        这个线程只是等结果。

        ⚠️ 前提是**工具处理函数不跑在那个循环的线程里**（FastAPI 把同步
        路由丢进 threadpool，AgentLoop 整个是同步的，所以成立）。
        万一哪天变了，下面那个 `is_running()` 分支会说清楚，
        而不是死锁。
        """
        return self._sync(lambda: self.acall(tool, args, timeout), timeout, tool)

    def _sync(
        self,
        make_coro: Any,
        timeout: float | None,
        label: str,
    ) -> CallResult:
        """把协程投回 WS 自己的循环，在这个线程等结果。

        ⚠️ 收的是**造协程的函数**，不是协程本身 —— 下面有几条提前返回的路，
        协程造出来又不 await 会留一个 "coroutine was never awaited" 警告，
        而那种警告在日志里看着像别的问题。
        """
        loop = self._loop
        if loop is None:
            return CallResult(False, error="她的电脑现在没连上")
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            # 在 WS 自己的循环里调同步接口 → 投进去再等会死锁。
            # 这条路该用 `acall`
            return CallResult(
                False,
                error=f"{self.name}.{label}: 在事件循环里请用 acall，不要用 call",
            )
        try:
            future = asyncio.run_coroutine_threadsafe(make_coro(), loop)
            #: 比里面那层多留 5 秒 —— 让它的超时先说话，
            #: 它的错误信息比这里的「超时」具体
            inner = CALL_TIMEOUT_S if timeout is None else timeout
            return future.result(inner + 5)
        except Exception as exc:  # noqa: BLE001
            return CallResult(False, error=f"{self.name}.{label}: {type(exc).__name__}: {exc}")

    async def list_tools(self) -> CallResult:
        return await self._request("tools/list", None)

    async def _request(
        self,
        method: str,
        params: dict[str, Any] | None,
        timeout: float | None = None,
    ) -> CallResult:
        ws = self._ws
        if ws is None:
            # 不是错误，是常态 —— 她的电脑关着而已
            return CallResult(False, error="她的电脑现在没连上")

        mid = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[mid] = _Pending(future)

        frame: dict[str, Any] = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            frame["params"] = params

        wait = CALL_TIMEOUT_S if timeout is None else timeout
        try:
            await ws.send_text(json.dumps(frame, ensure_ascii=False))
            msg = await asyncio.wait_for(future, wait)
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            return CallResult(False, error=f"{method} 超时（{wait:.0f} 秒）")
        except Exception as exc:  # noqa: BLE001
            self._pending.pop(mid, None)
            return CallResult(False, error=f"{method}: {type(exc).__name__}: {exc}")

        if "error" in msg:
            err = msg["error"] or {}
            # ⚠️ 把 data.reason 带出来 —— 「她拒绝了」和「她没看见」
            # 对他来说是两件事（架构文档第四节）
            reason = (err.get("data") or {}).get("reason")
            text = err.get("message", "执行失败")
            return CallResult(False, error=f"{text}（{reason}）" if reason else text)

        return CallResult(True, text=_text_of(msg.get("result")))


def _parse_at(value: Any) -> datetime:
    """摘要里的时间。解析不了就用现在 —— 时间不对总比不记好。"""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


async def _safe_close(ws: Any) -> None:
    try:
        await ws.close()
    except Exception:  # noqa: BLE001
        pass


def _text_of(result: Any) -> str:
    """把 MCP 的返回值压成一段文本，形状对齐 `mcp_client._text_of`。"""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict)]
        joined = "\n".join(p for p in parts if p)
        if joined:
            return joined
    return json.dumps(result, ensure_ascii=False)
