"""任务型纸条 —— 2026-08-18 空调那个 bug 的回归测试。

原始现场（线上日志）：

    00:06:33  留了张纸条：30 分钟后回来看看
              —— 半小时前给她开了主卧空调23度，她睡了，到点该把空调关上
    00:07:32  她回话了：撤掉 1 条纸条

纸条活了 59 秒。三条独立的原因，每条都足以单独让它失败：

1. `cancel_for()` 不分类型，她一开口全撤
2. `add()` 找已有纸条时不分类型，新的追问纸条会把任务纸条**改期并覆盖 why**
3. 唤醒 prompt 只允许「说 / 不说 / 结束」，他就算调了工具、没写话，
   也会被判成 PASS —— 做了事等于没做
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from attention.care.signal import FOLLOWUP, TASK
from attention.wakeup import WakeBook, parse_decision

T0 = datetime(2026, 8, 18, 0, 6, tzinfo=timezone.utc)


# ---------------------------------------------------------------- ① 撤销


def test_她回话不撤任务纸条():
    book = WakeBook()
    task = book.add("s1", "把主卧空调关掉", 30, now=T0, kind=TASK)
    chat = book.add("s1", "她去洗澡了", 30, now=T0, kind=FOLLOWUP)

    n = book.cancel_for("s1")

    assert n == 1, "只该撤掉追问型那张"
    assert task.alive, "空调那张必须活着 —— 这就是 2026-08-18 的 bug"
    assert not chat.alive


def test_追问纸条照旧会被撤():
    book = WakeBook()
    w = book.add("s1", "她去吃饭了", 40, now=T0)
    assert book.cancel_for("s1") == 1
    assert not w.alive


# ---------------------------------------------------------------- ② 改期串台


def test_两种纸条可以并存不互相覆盖():
    """新留追问纸条，不许把任务纸条改期 + 覆盖 why。"""
    book = WakeBook()
    task = book.add("s1", "把主卧空调关掉", 30, now=T0, kind=TASK)
    book.add("s1", "她去洗澡了", 20, now=T0, kind=FOLLOWUP)

    assert task.why == "把主卧空调关掉", "任务纸条的 why 被追问纸条覆盖了"
    assert len([w for w in book.all() if w.alive]) == 2


def test_同类纸条仍然是改期不叠加():
    book = WakeBook()
    a = book.add("s1", "她去吃饭了", 40, now=T0)
    b = book.add("s1", "她说大概一小时", 60, now=T0)
    assert a.id == b.id, "同类型该改期，不该叠加成两张"


# ---------------------------------------------------------------- ③ DONE


def test_DONE_标记正文可以为空():
    """他去关了个空调，不一定非要汇报一句。"""
    d = parse_decision("[DONE]")
    assert d.action == "done"
    assert d.text == ""


def test_DONE_可以带一句话():
    d = parse_decision("空调关啦，睡吧\n[DONE]")
    assert d.action == "done"
    assert d.text == "空调关啦，睡吧"


def test_没有标记时仍然默认继续链():
    """这条不能改 —— 他偶尔忘了写标记，默认停掉会让链静默断掉。"""
    d = parse_decision("那我等会儿再看看")
    assert d.action == "speak"
    assert d.next_after_min == 60


# ---------------------------------------------------------------- 存读


def test_kind_存得住():
    book = WakeBook()
    w = book.add("s1", "关空调", 30, now=T0, kind=TASK)
    back = WakeBook.from_list(book.to_list())
    got = back.get(w.id) if hasattr(back, "get") else None
    got = got or next(x for x in back.all() if x.id == w.id)
    assert got.kind == TASK
    assert got.closes_on_reply is False


def test_老数据没有kind一律当追问型():
    """加 kind 之前存下来的纸条，语义就是追问型。"""
    raw = {
        "id": "wake-old",
        "session_id": "s1",
        "why": "她去吃饭了",
        "wake_at": (T0 + timedelta(minutes=30)).isoformat(),
        "created_at": T0.isoformat(),
    }
    book = WakeBook.from_list([raw])
    w = next(iter(book.all()))
    assert w.kind == FOLLOWUP
    assert w.closes_on_reply is True


# ---------------------------------------------------------------- baseline


def test_rebase_校准所有活着的纸条():
    """两种纸条并存时，只校准找到的第一张会漏掉另一张 ——
    而漏掉的那张会在下次唤醒时被判成「她已经回话」，静默作废。"""
    book = WakeBook()
    task = book.add("s1", "关空调", 30, now=T0, kind=TASK)
    chat = book.add("s1", "她去洗澡了", 20, now=T0, kind=FOLLOWUP)

    later = T0 + timedelta(minutes=1)
    assert book.rebase("s1", later) is True
    assert task.baseline == later
    assert chat.baseline == later
