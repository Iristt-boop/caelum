"""待办写入的文本处理测试。

这些是纯文本操作，但写岔了会毁掉糖糖真正在用的清单 ——
所以拿她 todo.md 的真实结构（进行中/近期/定期/已完成）来测。
"""

from __future__ import annotations

import pytest

from tools.github_obsidian import GithubObsidianError
from tools.todo import TodoWriter, _find_undone, _section_end, _section_start

SAMPLE = """\
# 糖糖的待办清单

> 由小克维护。糖糖随口说，小克记录；每天早上小克推送当日提醒。
> 格式：- [ ] 未完成 / - [x] 已完成

## 进行中

- [ ] Nox数据记录系统 — 第二套存储
- [ ] 共感娃娃+FSR触觉传感器 — 黑羊已买，待购FSR402传感器

## 近期

- [ ] 护照领取 — 邮寄到家，约8月7日左右

## 定期

## 已完成

- [x] 坚果云同步（8月4日完成）
"""


class FakeGithub:
    """记下每次 PUT 的正文，不碰网络。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.sha = "sha-1"
        self.puts: list[str] = []
        self.fail_first_with_409 = False

    def read(self, _path):
        return self.content, self.sha

    def _api(self, _method, _path, body):
        import base64

        if self.fail_first_with_409:
            self.fail_first_with_409 = False
            raise GithubObsidianError("GitHub 返回 409: sha 过期")
        text = base64.b64decode(body["content"]).decode("utf-8")
        self.puts.append(text)
        self.content = text
        return 200, {}


def _writer(content: str = SAMPLE) -> tuple[TodoWriter, FakeGithub]:
    w = TodoWriter("tok", "owner/repo", "todo.md")
    fake = FakeGithub(content)
    w.gh = fake  # type: ignore[assignment]
    return w, fake


# ------------------------------------------------------------------ 定位


def test_section_start_skips_blank_line_after_heading():
    lines = SAMPLE.splitlines()
    assert lines[_section_start(lines, "进行中")].startswith("- [ ] Nox数据记录系统")


def test_section_end_is_after_last_item_not_before_next_heading():
    lines = SAMPLE.splitlines()
    # 「进行中」两条，插入点应该在第二条之后，而不是隔着空行贴到下一个 ## 前
    at = _section_end(lines, "进行中")
    assert lines[at - 1].startswith("- [ ] 共感娃娃")


def test_empty_section_still_locatable():
    lines = SAMPLE.splitlines()
    assert _section_start(lines, "定期") is not None


# ------------------------------------------------------------------ 增


def test_add_appends_to_section_tail_keeping_order():
    w, fake = _writer()
    msg = w.add("买菜", "近期")
    out = fake.puts[-1].splitlines()
    i = out.index("- [ ] 买菜")
    assert out[i - 1].startswith("- [ ] 护照领取")   # 接在原有条目后面
    assert "近期" in msg


def test_add_defaults_to_recent_when_section_bogus():
    w, fake = _writer()
    w.add("随便一件事", "不存在的区")
    body = fake.puts[-1]
    recent = body.split("## 近期")[1].split("##")[0]
    assert "随便一件事" in recent


def test_add_creates_missing_section_rather_than_dropping_item():
    w, fake = _writer(SAMPLE.replace("## 定期\n\n", ""))
    w.add("每周称体重", "定期")
    assert "## 定期" in fake.puts[-1]
    assert "每周称体重" in fake.puts[-1]


def test_add_does_not_touch_other_sections():
    w, fake = _writer()
    w.add("新事", "近期")
    ongoing = fake.puts[-1].split("## 进行中")[1].split("##")[0]
    assert "Nox数据记录系统" in ongoing and "新事" not in ongoing


# ------------------------------------------------------------------ 改


def test_complete_moves_item_into_done_section():
    w, fake = _writer()
    msg = w.complete("护照").message
    body = fake.puts[-1]
    assert "- [ ] 护照领取" not in body           # 原处已移除
    done = body.split("## 已完成")[1]
    assert "护照领取" in done and "- [x]" in done
    assert "护照" in msg


def test_complete_reports_ambiguity_without_writing():
    w, fake = _writer()
    msg = w.complete("— ").message                        # 三条都带破折号说明
    assert "换个更准的关键词" in msg
    assert not fake.puts                          # 拿不准就不写


def test_complete_reports_miss_without_writing():
    w, fake = _writer()
    msg = w.complete("压根没有这条").message
    assert "没找到" in msg
    assert not fake.puts


def test_complete_ignores_already_done_items():
    """「已完成」区里的不该被再次匹配到 —— 否则会说找到了却改不动。"""
    w, _ = _writer()
    assert "没找到" in w.complete("坚果云").message


def test_complete_signals_changed_flag_honestly():
    """changed 必须跟「真的改了没」一致 —— App 靠它判断有没有同步上。

    以前只回一句话，接口一律当成功：她在 App 里勾掉了、todo.md 没动，
    两边都不知道。
    """
    w, _ = _writer()
    assert w.complete("护照").changed is True
    w2, _ = _writer()
    assert w2.complete("压根没有这条").changed is False
    w3, _ = _writer()
    assert w3.complete("— ").changed is False


def test_complete_reads_only_once():
    """匹配和改写在同一次读里做完。

    分两次读的时候，GitHub 还没把刚写的内容传播过来就会「刚才明明找到了、
    现在匹配变了」—— 实测 App 里加一条立刻勾掉必翻车。
    """
    w, fake = _writer()
    reads = []
    orig = fake.read
    fake.read = lambda p: (reads.append(p), orig(p))[1]  # type: ignore[assignment]
    w.complete("护照")
    assert len(reads) == 1


def test_complete_is_case_insensitive():
    w, fake = _writer()
    w.complete("fsr402")
    assert "共感娃娃" in fake.puts[-1].split("## 已完成")[1]


# ------------------------------------------------------------------ 并发


def test_409_retries_once_then_succeeds():
    """routines 在这中间改了文件 —— 重读一遍再写，不能把她那条覆盖掉。"""
    w, fake = _writer()
    fake.fail_first_with_409 = True
    w.add("买菜", "近期")
    assert len(fake.puts) == 1
    assert "买菜" in fake.puts[-1]


def test_second_409_is_reported_not_swallowed():
    class AlwaysConflict(FakeGithub):
        def _api(self, _m, _p, _b):
            raise GithubObsidianError("GitHub 返回 409: sha 过期")

    w = TodoWriter("tok", "owner/repo")
    w.gh = AlwaysConflict(SAMPLE)  # type: ignore[assignment]
    with pytest.raises(GithubObsidianError, match="409"):
        w.add("买菜")


def test_empty_file_refuses_to_write():
    """读回来是空的多半是读失败，这时候写会把整份清单冲掉。"""
    w, fake = _writer("")
    with pytest.raises(GithubObsidianError):
        w.add("买菜")
    assert not fake.puts


# ------------------------------------------------------------------ 完整性


def test_file_stays_parseable_after_roundtrip():
    """加一条再划掉，剩下的条目一条不少。"""
    w, fake = _writer()
    before = len(_find_undone(SAMPLE.splitlines(), ""))
    w.add("临时事项", "近期")
    w.complete("临时事项")
    after = len(_find_undone(fake.puts[-1].splitlines(), ""))
    assert after == before
    assert fake.puts[-1].endswith("\n")
