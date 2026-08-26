"""GitHub 写笔记的 sha 重试。

2026-08-05 实测踩到：新建一篇笔记、紧接着追加一段，追加必 422。
不是并发冲突 —— 是 GitHub 写完之后 Contents API 有几百毫秒读不回新内容，
read() 拿到 404 就不带 sha，而文件其实在，于是 "sha wasn't supplied"。
"""

from __future__ import annotations

import base64

import pytest

from tools.github_obsidian import GithubObsidian, GithubObsidianError


class FakeApi:
    """模拟 GitHub 的读写延迟：文件写进去了，但接下来 N 次读还看不到。"""

    def __init__(self, lag_reads: int = 0, exists: str | None = None) -> None:
        self.content = exists
        self.sha = "sha-1" if exists is not None else None
        self.lag = lag_reads
        self.puts: list[dict] = []

    def read(self, _path):
        if self.content is None:
            return "", ""
        if self.lag > 0:
            self.lag -= 1
            return "", ""          # 还没传播过来
        return self.content, self.sha

    def api(self, _method, _path, body):
        # GitHub 的真实行为：文件存在却没给 sha → 422
        if self.content is not None and not body.get("sha"):
            raise GithubObsidianError('GitHub 返回 422: "sha" wasn\'t supplied.')
        if self.content is not None and body.get("sha") != self.sha:
            raise GithubObsidianError("GitHub 返回 409: sha 过期")
        self.puts.append(body)
        self.content = base64.b64decode(body["content"]).decode("utf-8")
        self.sha = f"sha-{len(self.puts) + 1}"
        return 200, {}


def _gh(fake: FakeApi) -> GithubObsidian:
    g = GithubObsidian("tok", "owner/repo", timeout=1)
    g.read = fake.read          # type: ignore[assignment]
    g._api = fake.api           # type: ignore[assignment]
    return g


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("tools.github_obsidian.time.sleep", lambda _s: None)


def test_新建之后紧接着追加_不该_422():
    """这就是实测翻车的那条路。"""
    fake = FakeApi(lag_reads=1, exists="# 测试\n原有内容")
    _gh(fake).append("a.md", "追加的一行。")
    assert "追加的一行" in fake.content
    assert "原有内容" in fake.content    # 没把原文冲掉


def test_延迟散去后带上正确的_sha():
    fake = FakeApi(lag_reads=1, exists="原文")
    _gh(fake).append("a.md", "新增")
    assert fake.puts[-1]["sha"] == "sha-1"


def test_覆盖已有文件_读延迟时也要成功():
    fake = FakeApi(lag_reads=2, exists="旧内容")
    _gh(fake).write("a.md", "全新内容")
    assert fake.content == "全新内容"


def test_新建全新文件_不带_sha_正常():
    fake = FakeApi(exists=None)
    _gh(fake).write("新的.md", "内容")
    assert fake.content == "内容"
    assert "sha" not in fake.puts[-1]


def test_一直读不到就如实报错_不能假装写好了():
    fake = FakeApi(lag_reads=99, exists="原文")
    with pytest.raises(GithubObsidianError, match="422"):
        _gh(fake).append("a.md", "x")
    assert not fake.puts


def test_非_sha_错误不重试():
    """403 没权限重试多少次都一样，别浪费时间也别掩盖原因。"""
    calls = []

    def always_403(_m, _p, _b):
        calls.append(1)
        raise GithubObsidianError("GitHub 返回 403: 没有写权限")

    fake = FakeApi(exists="原文")
    g = _gh(fake)
    g._api = always_403         # type: ignore[assignment]
    with pytest.raises(GithubObsidianError, match="403"):
        g.write("a.md", "x")
    assert len(calls) == 1
