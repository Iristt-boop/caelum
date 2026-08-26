"""一个够用的同步 REST 客户端。

用标准库而不是 httpx：Core 要调的几个服务里，三个在本机 localhost
（bridge / co-reading / app tracker），只有 Notion 出网。少一个依赖，
少一处会炸的地方。

各家的鉴权头不一样，所以做成可配置的：
    bridge      X-Nox-Token: <token>
    co-reading  Authorization: Bearer <token>
    Notion      Authorization: Bearer <token> + Notion-Version

错误一律**返回** RestResult 而不是抛 —— 调用方（工具）自己决定
是转成给模型看的话，还是 raise 让 loop 走「工具失败」那条路。
"""

from __future__ import annotations

import gzip
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RestResult:
    ok: bool
    data: Any = None
    error: str | None = None

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class RestClient:
    base: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 10.0
    # 401/403 时补一句提示。各家说法不同，让调用方自己填
    auth_hint: str = ""

    def __post_init__(self) -> None:
        self.base = self.base.rstrip("/")

    def _request(self, method: str, path: str, body: dict | None = None) -> RestResult:
        url = f"{self.base}{path}"
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in self.headers.items():
            req.add_header(k, v)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                # 有的服务**不管你要不要都返回 gzip** —— 和风天气就是
                # （2026-08-03：直接 decode 会炸在 `0x8b`，那是 gzip 魔数
                # `1f 8b` 的第二个字节，报错信息完全指不到压缩上）。
                # 按响应头判断，没说 gzip 的照常走，对其他调用方零影响。
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)
                raw = body.decode("utf-8")
                return RestResult(True, json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:200]
            except Exception:  # noqa: BLE001
                pass
            hint = f"（{self.auth_hint}）" if exc.code in (401, 403) and self.auth_hint else ""
            return RestResult(False, error=f"HTTP {exc.code}{hint}: {detail}")
        except urllib.error.URLError as exc:
            return RestResult(False, error=f"连不上 {self.base}: {exc.reason}")
        except Exception as exc:  # noqa: BLE001
            return RestResult(False, error=f"{type(exc).__name__}: {exc}")

    def get(self, path: str, params: dict | None = None) -> RestResult:
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            if clean:
                sep = "&" if "?" in path else "?"
                path = f"{path}{sep}{urllib.parse.urlencode(clean)}"
        return self._request("GET", path)

    def post(self, path: str, body: dict | None = None) -> RestResult:
        return self._request("POST", path, body or {})

    def delete(self, path: str) -> RestResult:
        return self._request("DELETE", path)
