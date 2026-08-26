"""用 GitHub Contents API 读写 Obsidian 库 —— 让 Nox 不依赖电脑。

## 为什么从坚果云换到 GitHub（2026-08-04）

坚果云 WebDAV 免费版限并发 3 连接，Remotely Save 首次全量同步直接 503，
糖糖不想再折腾坚果云。GitHub 方案：
  - 私有仓库免费、无限流量、稳定
  - Nox 本来就有 GITHUB_TOKEN（读 todo.md 用的那个），还测过有写权限
  - 电脑 Obsidian 用 Obsidian Git 插件自动 commit+push
  - 手机 iPhone 用 Working Copy（付费）clone 仓库

## GitHub 没有「目录」概念

PUT `/contents/阅读笔记/哲学大纲.md` 会自动创建中间路径，不用像 WebDAV
那样一层层 MKCOL —— 反而更简单。

## 追加没有原子操作

append = GET 读回来（拿 sha）→ 拼好 → PUT 带 sha 覆盖。
GitHub 用 sha 做乐观锁。

**要重试**（2026-08-05 改）：原本这里写着「她一个人用，冲突概率低，不重试」，
但翻车的不是并发 —— 是 GitHub 自己的读写延迟。写完几百毫秒内 Contents API
读不回新内容，于是「新建笔记 → 紧接着追加」会 422（拿不到 sha 却又确实存在）。
见 `_put()`。

## 失败要如实说

403 = token 没写权限；409 = sha 过期（文件被别人改了）；404 = 文件不存在。
message 原样回给模型，再由他转告糖糖 —— 绝不假装写好了。
"""

from __future__ import annotations

import base64
import json
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class GithubObsidianError(RuntimeError):
    """GitHub 操作失败。message 会原样回给模型再念给糖糖。"""


class GithubObsidian:
    def __init__(self, token: str, repo: str, timeout: float = 20.0) -> None:
        """repo 形如 `owner/repo`。"""
        self.token = token
        self.repo = repo.strip().strip("/")
        self.timeout = timeout

    def _api(self, method: str, path: str,
             body: dict | None = None) -> tuple[int, dict]:
        url = "https://api.github.com/repos/" + self.repo + "/contents/" + quote(
            path.lstrip("/"), safe="/")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Nox-Core",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        data = json.dumps(body).encode("utf-8") if body else None
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8") or "{}"
                return resp.status, json.loads(raw)
        except HTTPError as exc:
            raw = exc.read().decode("utf-8") or "{}"
            try:
                detail = json.loads(raw)
            except Exception:
                detail = {"message": raw[:200]}
            raise GithubObsidianError(
                f"GitHub 返回 {exc.code}: {detail.get('message', '')}") from exc
        except URLError as exc:
            raise GithubObsidianError(
                f"GitHub 连不上: {type(exc).__name__}") from exc

    # ------------------------------------------------------------ 文件

    def read(self, path: str) -> tuple[str, str]:
        """返回 (文本, sha)。文件不存在时 sha 为 None。"""
        try:
            _, d = self._api("GET", path)
        except GithubObsidianError as exc:
            if "404" in str(exc):
                return "", ""
            raise
        content = d.get("content") or ""
        try:
            return base64.b64decode(content).decode("utf-8"), d.get("sha", "")
        except Exception as exc:  # noqa: BLE001
            raise GithubObsidianError(f"文件解码失败: {exc}") from exc

    def write(self, path: str, content: str, message: str | None = None) -> None:
        """写整份文件。已存在则覆盖（带 sha），不存在则新建。"""
        self._put(path, lambda _existing: content, message or f"write {path}")

    def append(self, path: str, content: str, message: str | None = None) -> None:
        """追加到末尾。读出来拼好再覆盖。"""
        self._put(
            path,
            lambda existing: (existing.rstrip() + "\n\n" + content) if existing else content,
            message or f"append {path}",
        )

    def _put(self, path: str, build, message: str) -> None:
        """读 → 拼 → 带 sha 写。sha 不对就重读一次再来。

        ⚠️ 为什么要重试（2026-08-05 实测踩到）：
        GitHub 写完之后，Contents API 有几百毫秒读不回刚写的内容。
        于是「新建一篇笔记，紧接着追加一段」会走成：
            read → 404 → sha 为空 → PUT 不带 sha → 422 "sha wasn't supplied"
        （文件明明在，只是刚才没读到。）
        同一个race 也会让「连着两次写同一篇」失败。
        422/409 都是 sha 对不上，等一下重读一遍就好。
        """
        for attempt in (1, 2, 3):
            existing, sha = self.read(path)
            body: dict = {
                "message": message,
                "content": base64.b64encode(
                    build(existing).encode("utf-8")).decode("ascii"),
            }
            if sha:
                body["sha"] = sha
            try:
                self._api("PUT", path, body)   # 200=覆盖, 201=新建，都成功
                return
            except GithubObsidianError as exc:
                stale = "422" in str(exc) or "409" in str(exc)
                if not stale or attempt == 3:
                    raise
                logger.warning("写 %s 时 sha 对不上（第 %d 次），重读后再试",
                               path, attempt)
                time.sleep(0.6 * attempt)
