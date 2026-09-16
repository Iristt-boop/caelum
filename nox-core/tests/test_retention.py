"""数据保留策略（排期 4.7）。

## 这些测试能挡什么

- **没见过的 type 被默认删掉** —— 这条是整个脚本的安全方向。
  漏写一条策略只该导致"留久了"，绝不能导致"删错了"
- 一刀切删掉低频但要紧的数据（月经周期 `days=400` 才查得到）
- 干跑其实动了数据
- `KEEP_FOREVER` 的表被当成 0 天（那等于全删）
- 删之前不备份
- 边界：正好在窗口上的那一行

## 挡不住什么

- 真实的库结构变化（这里用的是临时库，只验判断逻辑）
- 并发写（SQLite 自己排队，不是这个脚本的事）
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.retention import KEEP_FOREVER, Policy, run  # noqa: E402

NOW = datetime(2026, 9, 16, 12, 0, 0)


def _mkdb(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    """rows = [(type, observed_at_iso)]"""
    db = tmp_path / "world.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE observations (id TEXT, type TEXT, observed_at TEXT)")
    conn.executemany("INSERT INTO observations VALUES (?,?,?)",
                     [(f"id{i}", t, ts) for i, (t, ts) in enumerate(rows)])
    conn.commit()
    conn.close()
    return db


def _count(db: Path, type_: str | None = None) -> int:
    conn = sqlite3.connect(db)
    try:
        if type_ is None:
            return conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM observations WHERE type=?",
                            (type_,)).fetchone()[0]
    finally:
        conn.close()


def _policy(db: Path, **kw) -> Policy:
    kw.setdefault("default_days", KEEP_FOREVER)
    kw.setdefault("type_col", "type")
    return Policy(db=str(db), table="observations", time_col="observed_at", **kw)


def _ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------- 安全方向

def test_没见过的type默认留着(tmp_path):
    """🔴 整个脚本最要紧的一条。

    策略表里漏写一类，结果必须是「留着」而不是「删掉」。
    反过来的话，将来加一个新的 observation 类型 = 它会被静默清空，
    而且要过很久才有人发现。
    """
    db = _mkdb(tmp_path, [
        ("她在电脑上做的事", _ago(90)),
        ("将来才会有的新类型", _ago(365)),      # 老得不能再老
        ("menstrual", _ago(300)),
    ])
    run([_policy(db, by_type={"她在电脑上做的事": 30})], apply=True, now=NOW)

    assert _count(db, "她在电脑上做的事") == 0, "该删的没删"
    assert _count(db, "将来才会有的新类型") == 1, \
        "没写进策略的类型被删了 —— 默认方向错了，漏写一条就会丢数据"
    assert _count(db, "menstrual") == 1, "低频要紧数据被误删"


def test_月经周期不会被删(tmp_path):
    """`context/providers/health.py:90` 查的是 days=400。

    一刀切 90 天会把它删掉，而且**不报错** ——
    只表现成「他不记得上次是什么时候了」。
    """
    db = _mkdb(tmp_path, [("menstrual", _ago(d)) for d in (10, 100, 380)])
    run([_policy(db, by_type={"她在电脑上做的事": 30})], apply=True, now=NOW)
    assert _count(db, "menstrual") == 3


def test_整表都是永久保留时直接跳过(tmp_path):
    """没有任何一条要删 → 连扫都不用扫，但要说清楚为什么跳过。"""
    db = _mkdb(tmp_path, [("随便什么", _ago(9999))])
    r = run([_policy(db, default_days=KEEP_FOREVER, by_type={})], apply=True, now=NOW)
    assert _count(db) == 1
    assert r["deleted"] == 0
    assert "observations" in r["skipped"]


def test_KEEP_FOREVER不等于零天(tmp_path):
    """`None` 要当成「永久」，不能在某处退化成 0（那等于全删）。

    ⚠️ 这条原本写成「整张表都 KEEP_FOREVER」，而那种策略会命中 `run()`
    里的**整表跳过**分支，**根本走不到 `_delete`** —— 变异测试显示把
    `_delete` 里的 KEEP_FOREVER 改成 0 天，这条测试**照样绿**。
    它一直在为另一个原因通过。

    所以这里必须**混一个要删的类型进去**，逼 `run()` 走完整路径，
    再断言 KEEP_FOREVER 那类一条没少。
    """
    db = _mkdb(tmp_path, [
        ("要删的", _ago(9999)),
        ("永久的", _ago(9999)),
    ])
    r = run([_policy(db, by_type={"要删的": 30, "永久的": KEEP_FOREVER})],
            apply=True, now=NOW)
    assert _count(db, "要删的") == 0, "该删的没删 —— 这条测试没走到删的那段"
    assert _count(db, "永久的") == 1, "KEEP_FOREVER 被当成 0 天了"
    assert r["deleted"] == 1


# ---------------------------------------------------------------- 删得对

def test_按天数删(tmp_path):
    db = _mkdb(tmp_path, [
        ("她在电脑上做的事", _ago(1)),
        ("她在电脑上做的事", _ago(29)),
        ("她在电脑上做的事", _ago(31)),
        ("她在电脑上做的事", _ago(200)),
    ])
    run([_policy(db, by_type={"她在电脑上做的事": 30})], apply=True, now=NOW)
    assert _count(db) == 2, "删多了或删少了"


def test_边界那一行(tmp_path):
    """正好 30 天的留着，30 天零 1 秒的删掉 —— 判据是 `<`，不是 `<=`。"""
    db = _mkdb(tmp_path, [
        ("x", (NOW - timedelta(days=30)).isoformat()),
        ("x", (NOW - timedelta(days=30, seconds=1)).isoformat()),
    ])
    run([_policy(db, by_type={"x": 30})], apply=True, now=NOW)
    assert _count(db) == 1


def test_多个类型各按各的(tmp_path):
    db = _mkdb(tmp_path, [
        ("快的", _ago(40)),
        ("慢的", _ago(40)),
        ("留着的", _ago(40)),
    ])
    run([_policy(db, by_type={"快的": 30, "慢的": 90, "留着的": KEEP_FOREVER})],
        apply=True, now=NOW)
    assert _count(db, "快的") == 0
    assert _count(db, "慢的") == 1
    assert _count(db, "留着的") == 1


# ---------------------------------------------------------------- 干跑 / 备份

def test_干跑一个字节都不写(tmp_path):
    db = _mkdb(tmp_path, [("她在电脑上做的事", _ago(200))] * 5)
    r = run([_policy(db, by_type={"她在电脑上做的事": 30})], apply=False, now=NOW)
    assert _count(db) == 5, "干跑居然真删了"
    assert r["deleted"] == 5, "干跑要如实报「会删几条」，不能报 0"


def test_真删之前先备份(tmp_path):
    db = _mkdb(tmp_path, [("她在电脑上做的事", _ago(200))])
    run([_policy(db, by_type={"她在电脑上做的事": 30})], apply=True, now=NOW)
    backups = list(tmp_path.glob("world-before-retention-*.db"))
    assert len(backups) == 1, "删了却没备份"
    # 备份里必须还有那条 —— 备份成空文件等于没备份
    assert _count(backups[0]) == 1


def test_旧快照会被清掉(tmp_path):
    """🔴 这条脚本每周跑一次。快照不清的话，**保留策略自己会攒垃圾**。"""
    from scripts.retention import KEEP_BACKUPS, backup

    db = _mkdb(tmp_path, [("x", _ago(1))])
    made = []
    for i in range(KEEP_BACKUPS + 3):
        # backup() 的文件名精确到秒，同一秒会互相覆盖 —— 手动错开
        b = backup(db)
        b2 = b.with_name(f"world-before-retention-2026090{i}-000000.db")
        b.rename(b2)
        made.append(b2)

    newest = backup(db)   # 最后这次才会触发清理（文件名是真实时间，排序最靠后）
    left = sorted(tmp_path.glob("world-before-retention-*.db"))

    assert len(left) == KEEP_BACKUPS, f"留了 {len(left)} 份，应该是 {KEEP_BACKUPS}"
    # 🔴 留下的必须是**最新的**那几份，不是随便几份 ——
    #    清理按时间排序才有意义，随机删等于没有后悔余地
    assert newest in left, "刚做的那份被自己清掉了"
    assert made[0] not in left and made[1] not in left, "清掉的不是最旧的那几份"
    assert made[-1] in left, "最新的那几份里少了一份"


def test_干跑不备份(tmp_path):
    """干跑不该在她机器上撒一地备份文件。"""
    db = _mkdb(tmp_path, [("她在电脑上做的事", _ago(200))])
    run([_policy(db, by_type={"她在电脑上做的事": 30})], apply=False, now=NOW)
    assert list(tmp_path.glob("*-before-retention-*")) == []


# ---------------------------------------------------------------- 空集 / 缺库

# ---------------------------------------------------------------- 按会话删
#
# 🔴 这一组是糖糖问「到 180 天的时候会话直接清空？」逼出来的。
#    原来的实现是**按行删**，而线上有一段 4049 条的对话从 8 月开到现在 ——
#    按行删会从这段还活着的对话里一天一天往前啃，会话还在列表里，
#    开头没了，标题还会跟着变（`/api/conv-sessions` 的标题取第一条用户消息）。

def _mkconv(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    """rows = [(session_id, timestamp)]"""
    db = tmp_path / "bridge.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE conversations (id TEXT, role TEXT, content TEXT, timestamp TEXT)")
    conn.executemany("INSERT INTO conversations VALUES (?,'user','x',?)", rows)
    conn.commit()
    conn.close()
    return db


def _conv_count(db: Path, sid: str | None = None) -> int:
    conn = sqlite3.connect(db)
    try:
        if sid is None:
            return conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM conversations WHERE id=?",
                            (sid,)).fetchone()[0]
    finally:
        conn.close()


def _conv_policy(db: Path, days: int = 180) -> Policy:
    return Policy(db=str(db), table="conversations", time_col="timestamp",
                  group_col="id", default_days=days)


def test_还在用的对话一个字都不动(tmp_path):
    """🔴 整组的判据是**最新**那一条，不是每一行各算各的。

    线上那段主对话从 2026-08 开到现在。按行删的话，到期那天它会
    从开头被啃掉，而会话还活着 —— 比整段删掉更糟，因为没有任何提示。
    """
    db = _mkconv(tmp_path, [
        ("主对话", _ago(300)),   # 开头很老
        ("主对话", _ago(200)),
        ("主对话", _ago(1)),     # 但昨天还在说话
    ])
    run([_conv_policy(db)], apply=True, now=NOW)
    assert _conv_count(db, "主对话") == 3, \
        "还在用的对话被从开头啃了 —— 判据用成了按行删"


def test_整段都过期了才整段删(tmp_path):
    db = _mkconv(tmp_path, [
        ("老对话", _ago(400)),
        ("老对话", _ago(300)),   # 最新的也超过 180 天
        ("新对话", _ago(10)),
    ])
    run([_conv_policy(db)], apply=True, now=NOW)
    assert _conv_count(db, "老对话") == 0, "整段过期的没删掉"
    assert _conv_count(db, "新对话") == 1


def test_只差一天也不动(tmp_path):
    """最新那条正好 179 天 → 整段留着。边界必须清楚。"""
    db = _mkconv(tmp_path, [("x", _ago(400)), ("x", _ago(179))])
    run([_conv_policy(db)], apply=True, now=NOW)
    assert _conv_count(db) == 2


def test_按会话删也要报对条数(tmp_path):
    """干跑要说清楚「会删几条、几段」，不能只说几段。"""
    db = _mkconv(tmp_path, [("老", _ago(400))] * 7 + [("新", _ago(1))])
    r = run([_conv_policy(db)], apply=False, now=NOW)
    assert r["deleted"] == 7, f"报了 {r['deleted']} 条，应该是 7"
    assert _conv_count(db) == 8, "干跑动了数据"


def test_group和type不许同时用():
    """想不清楚的组合最容易变成事故 —— 直接不让用。"""
    with pytest.raises(ValueError):
        Policy(db="x.db", table="t", time_col="ts", group_col="id", type_col="type")


# ---------------------------------------------------------------- 出厂策略本身
#
# 🔴 上面那些验的是**机制**，这一组验的是**决定**。
#    保留多久、哪张表不许删，都是有人拍过板的 —— 被"顺手整理"掉时
#    要有东西红，否则这些理由只活在 commit message 里。

def _shipped(table: str):
    from scripts.retention import POLICIES
    hits = [p for p in POLICIES if p.table == table]
    assert len(hits) == 1, f"{table} 在策略表里出现了 {len(hits)} 次"
    return hits[0]


def test_出厂策略_usage_log不许删():
    """🔴 `/api/usage-stats` 的总花费是实时全表加出来的
    （`bridge/server.js:3922` `allRows = rowsOf("")`）。
    给它设保留天数 = 她的历史总花费开始悄悄变小，而且不会有任何提示。
    """
    p = _shipped("usage_log")
    assert p.default_days is KEEP_FOREVER, \
        "usage_log 被设了保留天数 —— 她的历史总花费会开始变小"
    assert not p.by_type, "usage_log 不该有按类型的例外"


def test_出厂策略_observations默认是留():
    """新出现的 observation 类型必须默认留着。

    反过来的话，将来加一类事实 = 它被静默清空，很久以后才有人发现。
    """
    p = _shipped("observations")
    assert p.default_days is KEEP_FOREVER, "observations 的默认方向反了"
    assert set(p.by_type) == {"她在电脑上做的事", "他在电脑上做的事"}, \
        "要删的类型变了 —— 确认这是有人拍过板的，不是顺手改的"
    assert p.days_for("menstrual") is KEEP_FOREVER, \
        "月经周期会被删 —— health.py 查它用的是 days=400"
    assert p.days_for("sleep_duration") is KEEP_FOREVER


def test_出厂策略_conversations留180天():
    """糖糖 2026-09-16 定的。改这个数要她再点一次头。"""
    p = _shipped("conversations")
    assert p.default_days == 180, f"对话保留天数被改成了 {p.default_days}"
    # 🔴 按会话删，不是按行删 —— 去掉 group_col 就会从活着的对话里往前啃
    assert p.group_col == "id", \
        "对话变成按行删了 —— 还在用的那段会被从开头吃掉"


def test_库不在不算通过(tmp_path, capsys):
    """🔴 库路径写错时，「没有过期的」和「全清干净了」长得一模一样。"""
    r = run([_policy(tmp_path / "根本没这个库.db", by_type={"x": 30})],
            apply=True, now=NOW)
    assert r["checked"] == 0
    assert r["missing"], "库不存在却没记下来"
    assert "不下结论" in capsys.readouterr().out
