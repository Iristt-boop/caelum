"""真测（配置层 P2c，设计稿第八节 + 22.3，2026-10-11）。

**走生产同一条路径**：adapter 由 `make_adapter` 建（套档案、前端 key 优先），外面包一层计量（账本里记成 `config-test`），
不是另写一个 `requests` 调用 —— 09-08 utility 拿错 key 一天 200 次 401、「表面上他好好的」，就是因为验证和生产不是同一条路。

| 测什么 | 怎么判 | 要避的坑 |
|---|---|---|
| chat   | **正文非空**（不是 HTTP 200） | 思考模型的 reasoning 和正文抢 max_tokens：200 但 content 空（`reasoning-tokens-eat-max-tokens`） |
| cache  | 同一个请求隔 2 秒再发一次，第二次命中 > 0 | 智谱异步建缓存，紧接着发命不中；DeepSeek「尽力而为」—— 所以 cache 失败**不影响** chat 的结论，单独一行 |
| vision | 发一块纯红方块，回答里有「红 / red」 | 10-11 实测 deepseek-v4-pro：200、图被悄悄丢掉、答「无法确定」—— 只判「非空」会被它骗过 |
| models | 拉型号列表（`/models`） | 只测「key 在不在」是假验证 |

结果只存结论（`test_results`），**不存回答正文**；错误信息里的 key 先抹掉（SDK 的 401 有时会把 key 的一截带出来）。
"""

from __future__ import annotations

import dataclasses
import logging
import re
import struct
import time
import zlib
from dataclasses import dataclass
from typing import Any, Callable

from settings_store.seed import resolve_key
from settings_store.store import SettingsStore

logger = logging.getLogger("settings_store.probe")

#: 同一目标同一种测试，1 分钟内不重复花钱（直接回上一次的结论）
REPEAT_WINDOW_S = 60
#: 缓存要有足够长的前缀才会建：OpenAI / 百炼 1024、智谱建议 500+、Claude 5.x 512。这段约 1.3K token
_PARA = ("这是 Caelum 配置层的连通测试。Caelum 是糖糖和 Nox 共享的那片天空；"
         "这段文字只用来让请求有一段足够长、逐字节不变的前缀，好看第二次请求有没有命中厂商的提示词缓存。"
         "它不代表任何对话内容，也不会进任何人的记忆。")
TEST_SYSTEM = "\n".join(f"{i:02d}. {_PARA}" for i in range(24)) + "\n\n你只需要按用户说的回一句很短的话。"
CHAT_PROMPT = "测试连通：请只回一个字「好」。"
VISION_PROMPT = "这张图是什么颜色？只回答一个颜色词。"
#: 思考模型的 reasoning 会吃掉 max_tokens：给足，并压低思考档位
MAX_TOKENS = 2048
DEPTH = "low"


@dataclass
class Result:
    kind: str           # chat | cache | vision | models
    ok: bool
    latency_ms: int
    detail: str
    reused: bool = False

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def red_png(size: int = 64) -> str:
    """一块纯红方块的 data URI（答案已知，才分得出「看见了」和「编了一句」）。"""
    raw = b"".join(b"\x00" + bytes((220, 20, 20)) * size for _ in range(size))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    import base64
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


_KEYLIKE = re.compile(r"(sk-|key-|Bearer\s+)[A-Za-z0-9._\-*]{6,}", re.I)


def scrub(text: str, secrets: list[str]) -> str:
    """错误信息里不许有 key：已知的 key 原样抹掉，长得像 key 的也抹掉；再截到 200 字。"""
    out = text or ""
    for s in secrets:
        if s and len(s) >= 6:
            out = out.replace(s, "***")
    out = _KEYLIKE.sub(lambda m: m.group(1) + "***", out)
    return out[:200]


def target_of(provider_id: str, model_name: str | None = None) -> str:
    return f"model:{provider_id}/{model_name}" if model_name else f"provider:{provider_id}"


def llm_for(store: SettingsStore, keys: Any, provider_id: str, model_name: str) -> Any:
    """按档案建一份 `LLMConfig`：地址 / 协议来自厂商行，key 前端 > 环境变量。"""
    from config import LLMConfig

    p = store.provider(provider_id)
    if p is None:
        raise LookupError(f"没有 {provider_id} 这一家")
    key = (keys.get(provider_id) if keys is not None else "") or resolve_key(list(p["key_envs"]), _env())
    return LLMConfig(provider=p["protocol"], model=model_name, api_key=key, base_url=p["base_url"],
                     max_tokens=MAX_TOKENS, timeout=35.0, max_retries=0)   # 最坏 4 次 × 35s + 2s < bridge 的 150s


def _env():
    import os
    return os.environ


class Prober:
    """可注入的部分（建 adapter、睡觉、拉型号）都在构造参数里，测试换成假的；线上用默认值。"""

    def __init__(self, store: SettingsStore, keys: Any = None, *,
                 make: Callable[[Any], Any] | None = None,
                 list_models: Callable[[Any], list[str]] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 cache_wait_s: float = 2.0) -> None:
        self.store, self.keys = store, keys
        self._make = make or _default_make
        self._list_models = list_models or _default_list_models
        self._sleep = sleep
        self.cache_wait_s = cache_wait_s

    # ------------------------------------------------------------ 入口

    def test_model(self, provider_id: str, model_name: str, *, vision: bool | None = None) -> list[Result]:
        """测一个型号：chat + cache；型号档案里有 vision（或显式要求）再加 vision。"""
        target = target_of(provider_id, model_name)
        llm = llm_for(self.store, self.keys, provider_id, model_name)
        if not llm.api_key:
            r = Result("chat", False, 0, "缺失：这一家没配 key")
            self._save(target, r)
            return [r]
        if vision is None:
            m = next((x for x in self.store.models()
                      if x["provider_id"] == provider_id and x["model_name"] == model_name), None)
            vision = bool(m and "vision" in (m.get("capabilities") or []))
        out = self._once(target, "chat", lambda: self._chat_and_cache(target, llm))
        if vision:
            out += self._once(target, "vision", lambda: [self._vision(llm)])
        return out

    def test_provider(self, provider_id: str, model_name: str | None = None) -> list[Result]:
        """测一家：拉型号列表 + 一次最小对话（用传进来的型号，否则挑这一家第一个能聊天的）。两步结论分开存。"""
        target = target_of(provider_id)
        p = self.store.provider(provider_id)
        if p is None:
            raise LookupError(f"没有 {provider_id} 这一家")
        if model_name is None:
            m = next((x for x in self.store.models() if x["provider_id"] == provider_id and x["enabled"]
                      and "chat" in (x.get("capabilities") or [])), None)
            model_name = m["model_name"] if m else None
        llm = llm_for(self.store, self.keys, provider_id, model_name or "")
        if not llm.api_key:
            r = Result("models", False, 0, "缺失：这一家没配 key")
            self._save(target, r)
            return [r]
        out = self._once(target, "models", lambda: [self._models(llm)])
        if model_name:
            out += self.test_model(provider_id, model_name)
        return out

    # ------------------------------------------------------------ 各项

    def _chat_and_cache(self, target: str, llm: Any) -> list[Result]:
        from agent.llm import Message

        adapter = self._make(llm)
        msgs = [Message(role="user", text=CHAT_PROMPT)]
        t0 = time.monotonic()
        try:
            turn = adapter.complete(msgs, [], system=TEST_SYSTEM, depth=DEPTH, max_tokens=MAX_TOKENS)
        except Exception as exc:  # noqa: BLE001 —— 测试本来就是要接住失败，但要把原因说出来
            return [Result("chat", False, _ms(t0), self._scrub(f"{type(exc).__name__}: {exc}", llm))]
        ms = _ms(t0)
        if turn.stop_reason == "error":
            return [Result("chat", False, ms, self._scrub(turn.error or "出错了", llm))]
        text = (turn.text or "").strip()
        if not text:
            return [Result("chat", False, ms,
                           f"HTTP 成功但正文是空的（输出 {turn.usage.output_tokens} token，多半被思考吃光了）")]
        chat = Result("chat", True, ms, f"回了 {len(text)} 字，输入 {turn.usage.input_tokens} token")

        self._sleep(self.cache_wait_s)
        t1 = time.monotonic()
        try:
            again = adapter.complete(msgs, [], system=TEST_SYSTEM, depth=DEPTH, max_tokens=MAX_TOKENS)
        except Exception as exc:  # noqa: BLE001
            return [chat, Result("cache", False, _ms(t1), self._scrub(f"第二次请求失败：{type(exc).__name__}: {exc}", llm))]
        hit = again.usage.cache_read_tokens
        total = max(again.usage.input_tokens, 1)
        if again.stop_reason == "error":
            return [chat, Result("cache", False, _ms(t1), self._scrub(f"第二次请求失败：{again.error}", llm))]
        if hit > 0:
            return [chat, Result("cache", True, _ms(t1), f"第二次命中 {hit} / {total} token")]
        return [chat, Result("cache", False, _ms(t1), f"第二次没命中（输入 {total} token）—— 这家的缓存可能要更久才建好")]

    def _vision(self, llm: Any) -> Result:
        from agent.llm import Message

        # 强制带图：这一项要问的正是「它到底看不看得见」，不能让档案先把图降成文字
        adapter = self._make(dataclasses.replace(llm, vision=True, cache_style=llm.cache_style or "none"))
        t0 = time.monotonic()
        try:
            turn = adapter.complete([Message(role="user", text=VISION_PROMPT, images=[red_png()])], [],
                                    depth=DEPTH, max_tokens=MAX_TOKENS)
        except Exception as exc:  # noqa: BLE001
            return Result("vision", False, _ms(t0), self._scrub(f"{type(exc).__name__}: {exc}", llm))
        if turn.stop_reason == "error":
            return Result("vision", False, _ms(t0), self._scrub(turn.error or "出错了", llm))
        text = (turn.text or "").strip()
        if "红" in text or "red" in text.lower():
            return Result("vision", True, _ms(t0), f"认出了红色（图算进 {turn.usage.input_tokens} token）")
        return Result("vision", False, _ms(t0),
                      f"没认出红色：「{text[:20]}」（输入 {turn.usage.input_tokens} token —— 太少多半是图被悄悄丢了）")

    def _models(self, llm: Any) -> Result:
        t0 = time.monotonic()
        try:
            names = self._list_models(llm)
        except Exception as exc:  # noqa: BLE001
            return Result("models", False, _ms(t0), self._scrub(f"{type(exc).__name__}: {exc}", llm))
        if not names:
            return Result("models", False, _ms(t0), "型号列表是空的")
        return Result("models", True, _ms(t0), f"拉到 {len(names)} 个型号")

    # ------------------------------------------------------------ 记账

    def _once(self, target: str, kind: str, run: Callable[[], list[Result]]) -> list[Result]:
        """1 分钟内同一目标同一项测过 → 直接回上一次（不再花钱）。"""
        last = self.store.last_test(target, kind)
        if last and last["age_s"] < REPEAT_WINDOW_S:
            out = [Result(kind, bool(last["ok"]), last["latency_ms"] or 0, last["detail"], reused=True)]
            if kind == "chat":
                c = self.store.last_test(target, "cache")
                if c and c["age_s"] < REPEAT_WINDOW_S:
                    out.append(Result("cache", bool(c["ok"]), c["latency_ms"] or 0, c["detail"], reused=True))
            return out
        results = run()
        for r in results:
            self._save(target, r)
        return results

    def _save(self, target: str, r: Result) -> None:
        self.store.log_test(target=target, kind=r.kind, ok=r.ok, latency_ms=r.latency_ms, detail=r.detail)
        logger.info("配置真测：%s %s %s（%d ms）%s", target, r.kind, "通过" if r.ok else "失败", r.latency_ms, r.detail)

    def _scrub(self, text: str, llm: Any) -> str:
        secrets = [llm.api_key]
        if self.keys is not None:
            try:
                secrets += list(self.keys.export_all().values())
            except Exception:  # noqa: BLE001
                logger.debug("抹 key 时读不到前端 key 文件", exc_info=True)
        return scrub(text, secrets)


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _default_make(llm: Any) -> Any:
    from agent import meter
    from agent.adapters import make_adapter

    return meter.tag(make_adapter(llm), "config-test")


def _default_list_models(llm: Any) -> list[str]:
    if llm.provider == "anthropic":
        import anthropic

        c = anthropic.Anthropic(api_key=llm.api_key, base_url=llm.base_url or None, timeout=30, max_retries=0)
        return [m.id for m in c.models.list()]
    from openai import OpenAI

    c = OpenAI(api_key=llm.api_key, base_url=llm.base_url or None, timeout=30, max_retries=0)
    return [m.id for m in c.models.list()]


# ------------------------------------------------------------------ 状态（给视图用）


def status_of(store: SettingsStore, target: str, key_configured: bool) -> dict[str, Any]:
    """徽章：缺失 / 未测 / 正常 / 失败（沿用 Aru 的状态词）。chat / models 定生死；cache、vision 另列。"""
    if not key_configured:
        return {"status": "缺失", "tests": {}}
    tests = {k: store.last_test(target, k) for k in ("models", "chat", "cache", "vision")}
    tests = {k: {"ok": bool(v["ok"]), "detail": v["detail"], "at": v["at"]} for k, v in tests.items() if v}
    core = [tests[k]["ok"] for k in ("models", "chat") if k in tests]
    status = "未测" if not core else ("正常" if all(core) else "失败")
    return {"status": status, "tests": tests}
