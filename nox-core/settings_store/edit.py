"""她改厂商档案（配置层 P2d，2026-10-11）。

规矩（设计稿第七、八、十二节）：
  · **新增 / 修改会影响请求的东西，先真测通过才保存**（那是会让线路坏掉的一步）。名字、备注、开关这类不发请求的不用测
  · **改地址必须重新填 key**：防止把旧 key 送到新地址去
  · **代码自带的（种子）不能改**：每次启动种子刷新会把它们盖回去，改了等于骗她。可以给它们填 key、在它们名下加型号
  · 她加的型号短名一律 `u-` 开头：代码清单（`config.models`）永远不用这个前缀，种子刷新时 UNIQUE 不会撞
  · 标「能看图」要先看图测试通过：标错成能看，图会被悄悄丢掉还每轮重发（v4-pro 那样）
  · 日志只记谁改了哪一项，不记 key
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from typing import Any

from settings_store.probe import Prober, Result, llm_for, target_of
from settings_store.store import SettingsStore, _now

logger = logging.getLogger("settings_store.edit")

USER_PREFIX = "u-"
CAPABILITIES = ("chat", "vision", "video", "tts", "stt", "image", "embedding")
CACHE_STYLES = ("auto_prefix", "passthrough", "explicit_breakpoint", "none")
PROTOCOLS = ("openai_compat", "anthropic")
#: 每个都要真测：3 个 × 35 秒 + 缓存那次 35 秒 + 2 秒 < bridge 的 150 秒
MAX_MODELS_PER_ADD = 3
VISION_TEST_FRESH_S = 7 * 24 * 3600

#: 添加厂商的预设：坑写进预设（智谱 /v4），不再靠读注释。`verified` = 我们自己真连过
PRESETS: list[dict[str, Any]] = [
    {"preset": "deepseek", "label": "DeepSeek", "protocol": "openai_compat",
     "base_url": "https://api.deepseek.com/v1", "cache_style": "auto_prefix", "verified": True,
     "note": "自动前缀缓存，留几小时到几天"},
    {"preset": "zhipu", "label": "智谱 GLM", "protocol": "openai_compat",
     "base_url": "https://open.bigmodel.cn/api/paas/v4", "cache_style": "auto_prefix", "verified": True,
     "note": "路径是 /v4 不是 /v1；缓存只留 1~3 分钟；GLM-5 系关不掉思考"},
    {"preset": "openrouter", "label": "OpenRouter", "protocol": "openai_compat",
     "base_url": "https://openrouter.ai/api/v1", "cache_style": "passthrough", "verified": True,
     "note": "Claude / Qwen 要打断点（会透传），OpenAI / DeepSeek / Gemini 自动"},
    {"preset": "anthropic", "label": "Anthropic", "protocol": "anthropic",
     "base_url": "", "cache_style": "explicit_breakpoint", "verified": False,
     "note": "原生接口：显式断点，1 小时档写入 2 倍、读 0.1 倍"},
    {"preset": "dashscope", "label": "阿里百炼", "protocol": "openai_compat",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "cache_style": "auto_prefix", "verified": True,
     "note": "隐式缓存自动（命中 0.2 倍）；显式断点更便宜但只留 5 分钟"},
    {"preset": "gemini", "label": "Google Gemini", "protocol": "openai_compat",
     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "cache_style": "auto_prefix",
     "verified": False, "note": "OpenAI 兼容入口（未真连过）；2.5 起隐式缓存；能看视频"},
    {"preset": "minimax", "label": "MiniMax", "protocol": "openai_compat",
     "base_url": "https://api.minimaxi.com/v1", "cache_style": "auto_prefix",
     "verified": False, "note": "国内站地址（未真连过）；缓存方式没核实"},
]

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")


class EditError(ValueError):
    """改不了。`status` 给接口用；`results` 是没通过的真测结论（给她看为什么）。"""

    def __init__(self, msg: str, status: int = 400, results: list[Result] | None = None) -> None:
        super().__init__(msg)
        self.status = status
        self.results = results or []


def presets(store: SettingsStore) -> list[dict[str, Any]]:
    rows = store.providers()
    here = {(p["base_url"] or "").rstrip("/") for p in rows if p["base_url"]}
    ids = {p["id"] for p in rows}
    # 按地址认；地址为空的（Anthropic 原生走 SDK 默认地址）按名字认
    return [{**p, "exists": ((p["base_url"] or "").rstrip("/") in here) if p["base_url"] else (p["preset"] in ids)}
            for p in PRESETS]


def short_name_for(store: SettingsStore, model_name: str) -> str:
    base = USER_PREFIX + (re.sub(r"[^a-z0-9.]+", "-", model_name.lower()).strip("-.") or "model")[:40]
    taken = {m["short_name"] for m in store.models() if m["short_name"]}
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


class Editor:
    def __init__(self, store: SettingsStore, keys: Any, prober: Prober | None = None) -> None:
        self.store, self.keys = store, keys
        self.prober = prober or Prober(store, keys)

    # ------------------------------------------------------------ 拉型号（不保存）

    def fetch_models(self, *, provider_id: str | None = None, protocol: str = "openai_compat",
                     base_url: str = "", key: str = "") -> list[str]:
        """拉型号列表给她挑。已有的厂商可以不带 key（用存着的）；新厂商要带。什么都不写。"""
        from config import LLMConfig

        if provider_id:
            llm = llm_for(self.store, self.keys, provider_id, "")
            if key:
                llm = dataclasses.replace(llm, api_key=key.strip())
        else:
            _check_protocol(protocol)
            llm = LLMConfig(provider=protocol, model="", api_key=(key or "").strip(), base_url=_url(base_url))
        if not llm.api_key:
            raise EditError("没有 key：填上 key 再拉")
        r = self.prober._models(llm)
        if not r.ok:
            raise EditError(f"拉不到型号：{r.detail}", results=[r])
        return sorted(self.prober._list_models(llm))

    # ------------------------------------------------------------ 只测不存（她点「测试回话」）

    def try_route(self, *, model: str, provider_id: str | None = None, protocol: str = "openai_compat",
                  base_url: str = "", key: str = "", cache_style: str = "auto_prefix") -> list[Result]:
        """拿一条还没保存的线路发一句很短的话。**什么都不写**（结论也不存 —— 存了也挂不到任何东西上）。"""
        from config import LLMConfig

        names = _model_names([model])
        if not names:
            raise EditError("先挑一个型号再测")
        if provider_id:
            llm = llm_for(self.store, self.keys, provider_id, names[0])
            if (key or "").strip():
                llm = dataclasses.replace(llm, api_key=key.strip())
        else:
            _check_protocol(protocol)
            _check_cache(cache_style)
            llm = LLMConfig(provider=protocol, model=names[0], api_key=(key or "").strip(), base_url=_url(base_url),
                            max_tokens=2048, timeout=35.0, max_retries=0, cache_style=cache_style)
        if not llm.api_key:
            raise EditError("要填 key")
        return self.prober._chat_and_cache("", llm, with_cache=False)

    # ------------------------------------------------------------ 厂商

    def add_provider(self, *, id: str, label: str, protocol: str, base_url: str, cache_style: str,
                     key: str, models: list[str], note: str = "") -> dict[str, Any]:
        from config import LLMConfig

        pid = (id or "").strip().lower()
        if not _ID.match(pid) or pid.startswith("env-"):
            raise EditError("名字只能用小写字母、数字和横线（2~32 个字）")
        if self.store.provider(pid) is not None:
            raise EditError(f"已经有 {pid} 这一家了", status=409)
        _check_protocol(protocol)
        _check_cache(cache_style)
        names = _model_names(models)
        if not names:
            raise EditError("至少挑一个型号：保存前要拿它真测一次")
        key = (key or "").strip()
        if not key:
            raise EditError("要填 key")
        url = _url(base_url)
        self._url_free(url)
        results = self._gate([LLMConfig(provider=protocol, model=n, api_key=key, base_url=url,
                                        max_tokens=2048, timeout=35.0, max_retries=0, cache_style=cache_style)
                              for n in names])
        now = _now()
        with self.store.transaction() as c:
            c.execute("INSERT INTO providers(id,label,protocol,base_url,cache_style,auth,key_envs,source,note,"
                      "created_at,updated_at) VALUES(?,?,?,?,?,'api_key','[]','user',?,?,?)",
                      (pid, (label or pid).strip(), protocol, url, cache_style, note.strip(), now, now))
            for n in names:
                self._insert_model(c, pid, n, now)
        self.keys.set(pid, key)
        self._log(pid, names, results)
        logger.info("配置审计：她加了一家 %s（%d 个型号）", pid, len(names))
        return {"provider": pid, "models": names, "results": [r.as_dict() for r in results]}

    def patch_provider(self, pid: str, *, label: str | None = None, note: str | None = None,
                       base_url: str | None = None, cache_style: str | None = None,
                       key: str | None = None) -> dict[str, Any]:
        p = self._user_provider(pid)
        url = _url(base_url) if base_url is not None else p["base_url"]
        url_changed = url.rstrip("/") != (p["base_url"] or "").rstrip("/")
        if url_changed:
            self._url_free(url, but=pid)
        if url_changed and not (key or "").strip():
            raise EditError("改了地址要重新填 key —— 不能把原来的 key 送到新地址去")
        if cache_style is not None:
            _check_cache(cache_style)
        affects = url_changed or bool((key or "").strip()) or (cache_style not in (None, p["cache_style"]))
        results: list[Result] = []
        if affects:
            m = next((x for x in self.store.models() if x["provider_id"] == pid and x["enabled"]), None)
            if m is None:
                raise EditError("这一家下面没有能用的型号，没法真测 —— 先加一个型号")
            base = llm_for(self.store, self.keys, pid, m["model_name"])
            cand = dataclasses.replace(base, base_url=url, api_key=(key or "").strip() or base.api_key,
                                       cache_style=cache_style or p["cache_style"])
            results = self._gate([cand])
        with self.store.transaction() as c:
            c.execute("UPDATE providers SET label=?, note=?, base_url=?, cache_style=?, updated_at=? WHERE id=?",
                      ((label if label is not None else p["label"]).strip() or pid,
                       (note if note is not None else p["note"]).strip(),
                       url, cache_style or p["cache_style"], _now(), pid))
        if (key or "").strip():
            self.keys.set(pid, key)
        if affects:
            self.store.forget_tests(pid)
            self._log(pid, [m["model_name"]], results)
        logger.info("配置审计：她改了 %s（%s）", pid, "要真测的改动" if affects else "名字 / 备注")
        return {"provider": pid, "tested": affects, "results": [r.as_dict() for r in results]}

    def delete_provider(self, pid: str) -> dict[str, Any]:
        self._user_provider(pid)
        with self.store.transaction() as c:
            n = c.execute("DELETE FROM models WHERE provider_id=?", (pid,)).rowcount
            c.execute("DELETE FROM providers WHERE id=?", (pid,))
        self.keys.clear(pid)
        self.store.forget_tests(pid)
        logger.info("配置审计：她删了 %s（连同 %d 个型号和 key）", pid, n)
        return {"provider": pid, "models_deleted": n}

    # ------------------------------------------------------------ 型号

    def add_models(self, pid: str, models: list[str]) -> dict[str, Any]:
        """在一家名下加型号（种子厂商也行）。每个都真测通过才保存。"""
        if self.store.provider(pid) is None:
            raise EditError(f"没有 {pid} 这一家", status=404)
        names = _model_names(models)
        have = {m["model_name"] for m in self.store.models() if m["provider_id"] == pid}
        names = [n for n in names if n not in have]
        if not names:
            raise EditError("这些型号已经都在了")
        results = self._gate([llm_for(self.store, self.keys, pid, n) for n in names])
        now = _now()
        with self.store.transaction() as c:
            for n in names:
                self._insert_model(c, pid, n, now)
        self._log(pid, names, results)
        logger.info("配置审计：她在 %s 名下加了 %d 个型号", pid, len(names))
        return {"provider": pid, "models": names, "results": [r.as_dict() for r in results]}

    def patch_model(self, model_id: int, *, label: str | None = None, capabilities: list[str] | None = None,
                    cache_style: str | None = None, cache_ttl: str | None = None,
                    enabled: bool | None = None, price: dict | None = None) -> dict[str, Any]:
        m = self._user_model(model_id)
        target = target_of(m["provider_id"], m["model_name"])
        caps = m["capabilities"]
        if capabilities is not None:
            bad = [c for c in capabilities if c not in CAPABILITIES]
            if bad or "chat" not in capabilities:
                raise EditError("能力只能从 " + " / ".join(CAPABILITIES) + " 里选，而且 chat 必须在")
            if "vision" in capabilities and "vision" not in caps:
                last = self.store.last_test(target, "vision")
                if not last or not last["ok"] or last["age_s"] > VISION_TEST_FRESH_S:
                    raise EditError("先点「测看图」，通过了才能标能看图 —— 标错了图会被悄悄丢掉")
            caps = list(dict.fromkeys(capabilities))
        if cache_style is not None and cache_style != "":
            _check_cache(cache_style)
        if cache_ttl not in (None, "", "1h"):
            raise EditError("缓存有效期只能是 1h 或者不填")
        new_style = m.get("cache_style") if cache_style is None else (cache_style or None)
        new_ttl = m.get("cache_ttl") if cache_ttl is None else cache_ttl
        results: list[Result] = []
        if (new_style, new_ttl) != (m.get("cache_style"), m.get("cache_ttl")):
            base = llm_for(self.store, self.keys, m["provider_id"], m["model_name"])
            p = self.store.provider(m["provider_id"])
            results = self._gate([dataclasses.replace(base, cache_style=new_style or p["cache_style"],
                                                      cache_ttl=new_ttl or "")])
        with self.store.transaction() as c:
            c.execute("UPDATE models SET label=?, capabilities=?, cache_style=?, cache_ttl=?, enabled=?, "
                      "price_json=?, updated_at=? WHERE id=?",
                      ((label if label is not None else m["label"]).strip() or m["model_name"],
                       json.dumps(caps), new_style, new_ttl,
                       (1 if enabled else 0) if enabled is not None else m["enabled"],
                       json.dumps(price) if price is not None else (json.dumps(m["price"]) if m["price"] else None),
                       _now(), model_id))
        if results:
            self._log(m["provider_id"], [m["model_name"]], results)
        logger.info("配置审计：她改了型号 %s/%s", m["provider_id"], m["model_name"])
        return {"model_id": model_id, "results": [r.as_dict() for r in results]}

    def delete_model(self, model_id: int) -> dict[str, Any]:
        m = self._user_model(model_id)
        with self.store.transaction() as c:
            c.execute("DELETE FROM models WHERE id=?", (model_id,))
        logger.info("配置审计：她删了型号 %s/%s", m["provider_id"], m["model_name"])
        return {"model_id": model_id}

    # ------------------------------------------------------------ 内部

    def _gate(self, llms: list[Any]) -> list[Result]:
        """每条候选线路真测 chat（+ cache）；任何一条 chat 没过 → 整个改动不保存。"""
        if len(llms) > MAX_MODELS_PER_ADD:
            raise EditError(f"一次最多 {MAX_MODELS_PER_ADD} 个（每个都要真测，花钱）")
        out: list[Result] = []
        for i, llm in enumerate(llms):
            if not llm.api_key:
                raise EditError("没有 key：这一家还没配 key，先填 key")
            rs = self.prober._chat_and_cache("", llm, with_cache=(i == 0))     # 缓存看第一个就够
            for r in rs:
                r.detail = f"{llm.model}：{r.detail}"
            out += rs
            if not next(r for r in rs if r.kind == "chat").ok:
                raise EditError(f"真测没通过，没有保存 —— {rs[0].detail}", results=out)
        return out

    def _log(self, pid: str, names: list[str], results: list[Result]) -> None:
        for n in names:
            for r in results:
                if r.detail.startswith(f"{n}："):
                    self.store.log_test(target=target_of(pid, n), kind=r.kind, ok=r.ok,
                                        latency_ms=r.latency_ms, detail=r.detail[len(n) + 1:])

    def _insert_model(self, c: Any, pid: str, name: str, now: str) -> None:
        # 能力只给 chat：没测过的型号不许标能看图（设计稿第六节「未知型号的安全默认」）
        c.execute("INSERT INTO models(provider_id,model_name,short_name,label,capabilities,origin,enabled,"
                  "created_at,updated_at) VALUES(?,?,?,?,?,'manual',1,?,?)",
                  (pid, name, short_name_for(self.store, name), name, json.dumps(["chat"]), now, now))

    def _url_free(self, url: str, but: str | None = None) -> None:
        """一个地址只许一家：档案按地址认厂商，两家同地址会拿错 key。"""
        other = next((p for p in self.store.providers()
                      if p["id"] != but and (p["base_url"] or "").rstrip("/") == url.rstrip("/")), None)
        if other is not None:
            raise EditError(f"这个地址已经是「{other['label']}」了：在它名下加型号、或者给它换 key 就行", status=409)

    def _user_provider(self, pid: str) -> dict[str, Any]:
        p = self.store.provider(pid)
        if p is None:
            raise EditError(f"没有 {pid} 这一家", status=404)
        if p["source"] != "user":
            raise EditError("这一家是代码自带的：改了下次重启会被代码盖回去。可以给它填 key、在它名下加型号",
                            status=409)
        return p

    def _user_model(self, model_id: int) -> dict[str, Any]:
        m = self.store.model(model_id)
        if m is None:
            raise EditError("没有这个型号", status=404)
        if m["origin"] == "seed":
            raise EditError("这个型号是代码自带的：改了下次重启会被代码盖回去", status=409)
        return m


def _url(u: str | None) -> str:
    u = (u or "").strip()
    if u and not re.match(r"^https://[^\s/?#]+(/[^\s?#]*)?$", u):
        raise EditError("地址要以 https:// 开头，不能带 ? 或 #（key 别塞进地址里）")
    return u.rstrip("/")


def _check_protocol(p: str) -> None:
    if p not in PROTOCOLS:
        raise EditError("协议只能是 openai_compat 或 anthropic")


def _check_cache(s: str) -> None:
    if s not in CACHE_STYLES:
        raise EditError("缓存打法只能是 " + " / ".join(CACHE_STYLES))


def _model_names(models: list[str]) -> list[str]:
    names = [m.strip() for m in (models or []) if m and m.strip()]
    if any(len(n) > 120 or any(ch.isspace() for ch in n) for n in names):
        raise EditError("型号名不能有空格，也不能太长")
    return list(dict.fromkeys(names))
