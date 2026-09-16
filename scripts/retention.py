#!/usr/bin/env python3
"""数据保留策略（排期 4.7）—— 只删该删的，不该删的连碰都不碰。

    python3 retention.py            # 干跑，一个字节都不写
    python3 retention.py --apply    # 真删（会先备份库文件）

## 🔴 先说结论：这条排期一开始的假设是错的

排期把它写成「三个最大头」，暗示是**磁盘问题**。实测（2026-09-16）：

    磁盘          59G，用了 24G（42%），剩 34G
    最大的库      embeddings.db 5.7M
    nox-bridge.db 2.8M ｜ sessions.db 2.6M ｜ world.db 1.4M

**一个都不大，按现在的速度十年也撑得住。** 所以真正的理由只有两个：

  1. **隐私** —— 原始对话正文无限期留着，机器一旦被拿下就是全部
  2. **噪音** —— 某一类数据涨得比别人快一个数量级，把有用的埋掉

三张表量下来是三个不同的答案，下面逐条说清楚**为什么**。

## 三张表，三个答案

### observations —— 要删，而且很安全

    她在电脑上做的事   2536 条  ← 占 91.5%，日均 150
    他在电脑上做的事    144 条
    sleep_duration     32 条
    daily_steps        20 条
    hrv                20 条
    menstrual           9 条   ← 两个月才 9 条
    其余                11 条

**涨的只有活动追踪那两类**，其余加起来一年也就几百条。

🔴 **所以必须按 type 分别定策，绝不能一刀切。**
`context/providers/health.py:90` 查月经周期用的是 **`days=400`** ——
一刀切 90 天会把她的周期史直接删掉，而且不会有任何报错，
只会表现成「他不记得上次是什么时候了」。

### usage_log —— **不删**，删了会静默改数

`bridge/server.js:3922` 的 `/api/usage-stats` 里：

    const allRows = rowsOf("");   // 没有时间过滤
    const total = sum(allRows);   // 她看到的「总花费」

**总花费是每次实时从原始行加出来的。** 删掉旧行 =
她的历史总花费**悄悄变小**，没有任何提示，而且再也对不回去。

而 usage_log 一行几十字节、一年 3.7 万行 ≈ 2MB。
**为了 2MB 去冒改错账的风险，这笔交易不划算。**
真要削的话正确做法是「先滚动汇总进 usage_daily，再改读的那一侧
把汇总并进去」—— 那是另一件事，不在这个脚本里偷偷做。

### conversations —— 留 180 天（糖糖 2026-09-16 定）

这是隐私问题，不是技术问题：原始聊天正文要留多久，得她说。她说半年。

理由站得住：**长期记忆在 OB 里**，删掉半年前的原始对话不影响他记不记得什么；
而真出事的时候，半年的暴露面比"从第一天起的全部"小得多。

⚠️ 定的当天一条都删不掉 —— 最早的记录是 2026-07，离 180 天还远。
这条策略要到 **2027 年 1 月**才第一次咬到东西。
（所以别拿"跑完删了 0 条"当它没生效的证据，用 `--now` 验。）

附带发现：`conv_fts` 是空的（0 行 vs 4588），全文索引建了从来没人写，
对话搜索一直是哑的。好处是删对话不需要同步索引 —— 没东西可同步。
（那个 bug 另算，不在 4.7 里。）

## 这个脚本能挡什么

- 一刀切删掉低频但要紧的数据（月经周期、体重、睡眠趋势）
- 新出现的数据类型被默认删掉 —— **默认是留，不是删**
- 干跑和真删混淆（默认干跑，`--apply` 才动手，动手前先备份）

## 挡不住什么

- 别人直接 `sqlite3 ... DELETE`（这只是一条定时清理，不是权限控制）
- 库正在被写的时候删（SQLite 会自己排队，但大事务可能等锁）
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------- 策略

#: `days=None` = **永久保留**。这是默认方向，也是安全方向：
#: 想删必须显式写出来，漏写只会导致"留久了"，不会导致"删错了"。
KEEP_FOREVER = None

# ================================================================
# 两道保险 —— 糖糖 2026-09-16 定的要求：
#
#   「不能我第二天看对话的时候直接什么都没有了。
#     而他也没有了昨天的记忆。要把我的体验感放在第一。」
#
# 保留天数写多少是**策略**，而策略可能写错、时间格式可能对不上、
# 时区可能差八小时。这两道保险不管策略说什么，先护住她眼前的东西。
# ================================================================

#: 🔴 **近期地板**：任何策略都不许删比这更新的东西。
#:
#: 就算某天有人把保留天数手滑写成 0、或者 cutoff 因为时区/格式算歪了，
#: 最近一周的对话和记忆也一定还在 —— 她第二天打开不会是空的，
#: 他也不会忘了昨天。策略算出来的 cutoff 比这更近就**直接拒绝跑**，
#: 不是"夹一下再跑"：那样会把一次配置事故变成一次静默的小规模删除。
RECENT_FLOOR_DAYS = 7

#: 🔴 **熔断**：一次运行删掉一张表超过这个比例，就中止、不删、报错。
#:
#: 正常情况下每周清掉的是一小撮过期数据。要是某一次算出来要删掉大半张表，
#: 那几乎一定是判据出了问题（时间格式变了、字段改名了、时区错了），
#: **而不是真有那么多东西过期**。这种时候正确的反应是停手喊人，不是照删。
MAX_SHARE = 0.5

#: 熔断只对**有规模的表**生效。
#:
#: 一张 3 行的表删掉 2 行是 67%，但那说明不了任何问题；
#: 3000 行删掉 2000 行才说明判据坏了。不设这个下限的话，
#: 熔断会在小表上天天误触发 —— 而**天天误报的告警等于没有告警**，
#: 真出事那次也会被当成又一次误报。
#: 小表不设防也不危险：上面那道近期地板照样护着最近一周。
MIN_ROWS_FOR_BREAKER = 100


class RetentionRefused(Exception):
    """保险拦下来了。**不许 catch 之后接着删。**"""


class Policy:
    """一张表的保留策略。

    `group_col` —— 🔴 **按组删，不是按行删。**

    对话是这么组织的：一个 `id` 下面挂着从头到尾所有消息。
    实测（2026-09-16）线上有一个会话 `1809ea16d4f7`：**4049 条，
    2026-08-06 开始，到今天还在用** —— 占全部消息的 88%。

    如果按行删，到期那天它会**从这段还活着的对话里，一天一天往前啃**：
    今天删掉 8 月 6 日那几条，明天删 8 月 7 日的……
    会话还在列表里，点进去开头没了，而且**没有任何提示**。
    `/api/conv-sessions` 的标题取的是"第一条用户消息"，
    所以连标题都会跟着变成中间某句话。

    设了 `group_col` 之后，判据变成**整组里最新的那一条**：
    只要这段对话最近还说过话，一个字都不动；
    整段都超过保留期了，才整段删掉。
    """

    def __init__(self, db: str, table: str, time_col: str, *,
                 default_days: int | None = KEEP_FOREVER,
                 type_col: str | None = None,
                 by_type: dict[str, int | None] | None = None,
                 group_col: str | None = None,
                 cascade: tuple[str, str] | None = None,
                 why: str = ""):
        self.db = Path(db)
        self.table = table
        self.time_col = time_col
        self.default_days = default_days
        self.type_col = type_col
        self.by_type = by_type or {}
        self.group_col = group_col
        #: `(表名, 主键列)` —— 整组删掉之后，把父表里对应的行也删掉。
        #:
        #: 🔴 不级联的话会留下**空壳会话**：`sessions` 里有这一行、
        #:    `messages` 里一条都没有，列表里看得见，点进去一片空白。
        #:    糖糖 2026-09-16：「只要别出现第二天整个会话都空了的情况就好」——
        #:    空壳正是那个样子，只是慢一点。
        self.cascade = cascade
        self.why = why
        if cascade and not group_col:
            # 级联是"这一组没了，父表那行也该没"，没有组的概念就无从谈起
            raise ValueError("cascade 必须和 group_col 一起用")
        if group_col and type_col:
            # 两个一起用的语义是"按组删但每组还分类型"，想不出真实用例，
            # 而想不清楚的组合最容易变成事故。要用再说。
            raise ValueError("group_col 和 type_col 暂不支持同时使用")

    def days_for(self, type_value: str | None) -> int | None:
        """某一行该留多久。**查不到就用 default**，而 default 是「永久」。"""
        if self.type_col and type_value in self.by_type:
            days = self.by_type[type_value]
        else:
            days = self.default_days
        if days is not KEEP_FOREVER and days < RECENT_FLOOR_DAYS:
            # 🔴 拒绝，不是夹一下 —— 夹一下会把配置事故变成静默的小规模删除
            raise RetentionRefused(
                f"{self.table} 的保留天数是 {days}，比地板 {RECENT_FLOOR_DAYS} 天还短。"
                f"这几乎一定是写错了 —— 拒绝执行。真要删这么近的东西，"
                f"先改 RECENT_FLOOR_DAYS 并说清楚为什么。")
        return days


#: 🔴 改这张表 = 改她的数据会被留多久。改之前先读上面那三段。
POLICIES: list[Policy] = [
    Policy(
        db="/root/nox-core/data/world.db",
        table="observations",
        time_col="observed_at",
        type_col="type",
        default_days=KEEP_FOREVER,          # ← 新 type 出现时默认留着
        by_type={
            # 只有这两类在涨（91.5% 的行）。30 天足够趋势用：
            # 最长的回看是 evaluator 的 sleep_duration days=14，
            # 而活动追踪没有任何地方做长回看。
            "她在电脑上做的事": 30,
            "他在电脑上做的事": 30,
        },
        why="活动追踪日均 150 条；健康类一年才几百条，一条都不删",
    ),
    Policy(
        db="/root/data/nox-bridge.db",
        table="usage_log",
        time_col="ts",
        default_days=KEEP_FOREVER,
        why="🔴 不删 —— /api/usage-stats 的总花费是实时全表加出来的，"
            "删旧行会让她的历史总花费悄悄变小。一年才 2MB，不值得冒这个险",
    ),
    Policy(
        db="/root/data/nox-bridge.db",
        table="conversations",
        time_col="timestamp",
        # 🔴 **按会话删，不是按行删。** 线上有一段 4049 条的对话从 8 月开到现在，
        #    按行删会从它中间往前啃，而会话还在列表里 —— 见 Policy 的文档。
        group_col="id",
        default_days=180,       # ← 糖糖 2026-09-16 定的
        why="原始聊天正文留半年。长期记忆在 OB 里，删这个不影响他记不记得；"
            "真出事时半年的暴露面比「全部」小得多",
    ),
    Policy(
        db="/root/nox-core/data/sessions.db",
        table="messages",
        time_col="created_at",
        # 和 bridge 的 conversations **同一段对话**（session id 都是同一个）——
        # 一份给 App 看，一份给他做上下文。两边用不同窗口会变成
        # 一边忘了一边还记得，排查时非常难受。所以同样 180 天、同样按会话删。
        group_col="session_id",
        # 🔴 连 `sessions` 那一行一起删。留着 = 空壳会话，
        #    列表里看得见、点进去一片空白。
        #    （会连带删掉那一行的 summary —— 摘要只为它自己那段对话服务，
        #      段没了摘要也没用了。糖糖 2026-09-16 拍的板。）
        cascade=("sessions", "id"),
        default_days=180,
        why="nox-core 侧的同一段对话，和 conversations 同口径；整段删干净不留空壳",
    ),
]


# ---------------------------------------------------------------- 干活

def _expired_groups(conn: sqlite3.Connection, p: Policy, cutoff: str) -> list[str]:
    """哪些组**整组**都过期了。判据是组里**最新**的那一条。

    🔴 `HAVING MAX(time) < cutoff` —— 不是 `WHERE time < cutoff`。
    后者会从还在用的对话里往前啃。
    """
    return [r[0] for r in conn.execute(
        f"SELECT {p.group_col} FROM {p.table} "
        f"GROUP BY {p.group_col} HAVING MAX({p.time_col}) < ?", (cutoff,))]


def _rows_to_delete(conn: sqlite3.Connection, p: Policy, now: datetime) -> dict[str, int]:
    """返回 {type: 条数}。不改任何东西。"""
    out: dict[str, int] = {}

    if p.group_col:
        days = p.days_for(None)
        if days is KEEP_FOREVER:
            return out
        cutoff = (now - timedelta(days=days)).isoformat()
        groups = _expired_groups(conn, p, cutoff)
        if not groups:
            return out
        qs = ",".join("?" * len(groups))
        n = conn.execute(
            f"SELECT COUNT(*) FROM {p.table} WHERE {p.group_col} IN ({qs})",
            groups).fetchone()[0]
        out[f"(整段对话 × {len(groups)})"] = n
        if p.cascade:
            ctable, ckey = p.cascade
            cn = conn.execute(
                f"SELECT COUNT(*) FROM {ctable} WHERE {ckey} IN ({qs})",
                groups).fetchone()[0]
            if cn:
                out[f"({ctable} 里对应的行)"] = cn
        return out

    if p.type_col:
        types = [r[0] for r in conn.execute(
            f"SELECT DISTINCT {p.type_col} FROM {p.table}")]
    else:
        types = [None]

    for t in types:
        days = p.days_for(t)
        if days is KEEP_FOREVER:
            continue
        cutoff = (now - timedelta(days=days)).isoformat()
        if p.type_col:
            n = conn.execute(
                f"SELECT COUNT(*) FROM {p.table} "
                f"WHERE {p.type_col} = ? AND {p.time_col} < ?", (t, cutoff)).fetchone()[0]
        else:
            n = conn.execute(
                f"SELECT COUNT(*) FROM {p.table} WHERE {p.time_col} < ?",
                (cutoff,)).fetchone()[0]
        if n:
            out[t or "(全表)"] = n
    return out


def _delete(conn: sqlite3.Connection, p: Policy, now: datetime) -> int:
    total = 0

    if p.group_col:
        days = p.days_for(None)
        if days is KEEP_FOREVER:
            return 0
        cutoff = (now - timedelta(days=days)).isoformat()
        groups = _expired_groups(conn, p, cutoff)
        if groups:
            qs = ",".join("?" * len(groups))
            cur = conn.execute(
                f"DELETE FROM {p.table} WHERE {p.group_col} IN ({qs})", groups)
            total = cur.rowcount
            if p.cascade:
                # 🔴 同一个事务里。父行留下来 = 空壳会话，
                #    而空壳正是「点进去一片空白」的那个样子。
                ctable, ckey = p.cascade
                cur = conn.execute(
                    f"DELETE FROM {ctable} WHERE {ckey} IN ({qs})", groups)
                total += cur.rowcount
        conn.commit()
        return total

    if p.type_col:
        types = [r[0] for r in conn.execute(
            f"SELECT DISTINCT {p.type_col} FROM {p.table}")]
    else:
        types = [None]

    for t in types:
        days = p.days_for(t)
        if days is KEEP_FOREVER:
            continue
        cutoff = (now - timedelta(days=days)).isoformat()
        if p.type_col:
            cur = conn.execute(
                f"DELETE FROM {p.table} "
                f"WHERE {p.type_col} = ? AND {p.time_col} < ?", (t, cutoff))
        else:
            cur = conn.execute(
                f"DELETE FROM {p.table} WHERE {p.time_col} < ?", (cutoff,))
        total += cur.rowcount
    conn.commit()
    return total


#: 留几份删除前快照。3 份 ≈ 三周的后悔余地。
KEEP_BACKUPS = 3


def backup(db: Path) -> Path:
    """删之前先拷一份。几 MB 的东西，没有不备份的理由。

    🔴 **必须用 SQLite 自己的备份接口，不能 `shutil.copy2`。**

    这几个库全是 WAL 模式，而且 WAL 文件有 4MB 上下 —— 比某些库文件本身还大。
    `copy2` 只拷 `.db`，**已提交但还没 checkpoint 的事务全都不在里面**。
    实测（2026-09-16）：`cp` 出来的 sessions.db 少了 220 条消息，
    nox-bridge.db 少了 195 条 —— 而且拷贝过程一声不吭，**只有真去恢复
    的那天才会发现少了东西**，那时候原件已经被删过了。

    `conn.backup()` 走的是在线备份接口，拿到的是一致快照（含 WAL 内容）。

    ⚠️ 顺手清掉旧快照 —— 这条是**每周**跑的，不清的话
    「保留策略」自己会在她机器上攒一地备份文件，那就成笑话了。
    """
    dest = db.with_name(f"{db.stem}-before-retention-"
                        f"{datetime.now():%Y%m%d-%H%M%S}{db.suffix}")
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    olds = sorted(db.parent.glob(f"{db.stem}-before-retention-*{db.suffix}"))
    for old in olds[:-KEEP_BACKUPS]:
        try:
            old.unlink()
        except OSError as e:  # noqa: PERF203
            # 不许静默：删不掉就说出来，否则表现成「磁盘慢慢满了」
            print(f"    ⚠️ 旧快照删不掉：{old.name}（{e}）")
    return dest


def run(policies: list[Policy], *, apply: bool, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    report: dict = {"checked": 0, "deleted": 0, "skipped": [], "missing": [],
                    "refused": [], "details": []}

    for p in policies:
        if not p.db.exists():
            report["missing"].append(str(p.db))
            print(f"⚠️ 库不在：{p.db} —— 跳过（**这不是通过**）")
            continue

        report["checked"] += 1
        conn = sqlite3.connect(str(p.db))
        try:
            # 整张表都是永久保留 → 一句话说清为什么，不要静悄悄跳过
            if p.default_days is KEEP_FOREVER and not any(
                    v is not KEEP_FOREVER for v in p.by_type.values()):
                print(f"[–] {p.table}：全部保留 —— {p.why}")
                report["skipped"].append(p.table)
                continue

            plan = _rows_to_delete(conn, p, now)
            if not plan:
                print(f"[✓] {p.table}：没有过期的")
                continue

            # 🔴 熔断。判据是**这张表自己的行数**，不是父表的 ——
            #    级联删掉的父行不算在分母里，否则比例会被稀释。
            total_rows = conn.execute(f"SELECT COUNT(*) FROM {p.table}").fetchone()[0]
            own = sum(n for k, n in plan.items() if not k.startswith("("
                      + (p.cascade[0] if p.cascade else "\x00")))
            if total_rows >= MIN_ROWS_FOR_BREAKER and own / total_rows > MAX_SHARE:
                msg = (f"{p.table} 这一次要删掉 {own}/{total_rows} 条"
                       f"（{own / total_rows:.0%}），超过 {MAX_SHARE:.0%} —— "
                       f"这几乎一定是判据出了问题（时间格式变了？字段改名了？时区错了？），"
                       f"不是真有那么多东西过期。**停手，一条都不删。**")
                print(f"🔴 {msg}")
                report["refused"].append(p.table)
                continue

            for t, n in sorted(plan.items(), key=lambda kv: -kv[1]):
                days = p.days_for(None if t == "(全表)" else t)
                print(f"    {'删' if apply else '会删'} {n:>6} 条  {t}（留 {days} 天）")
                report["details"].append({"table": p.table, "type": t, "n": n, "days": days})

            if apply:
                b = backup(p.db)
                print(f"    备份 → {b.name}")
                n = _delete(conn, p, now)
                report["deleted"] += n
                print(f"[✓] {p.table}：删了 {n} 条")
            else:
                report["deleted"] += sum(plan.values())
        finally:
            conn.close()

    # 🔴 空集不是通过：一个库都没查到，多半是路径错了，
    #    而那时候「没有过期的」看起来和「全清干净了」一模一样。
    if report["checked"] == 0:
        print("🔴 一个库都没查到 —— 路径对吗？不下结论。")

    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="数据保留策略（排期 4.7）")
    ap.add_argument("--apply", action="store_true", help="真删（默认只干跑）")
    # 🔴 这个参数是给**验证**用的，不是给运维用的。
    #    「没有过期的」和「策略压根没生效」长得一模一样 —— 空集不是通过。
    #    把时间往前拨，能逼它给出一个可区分的答案：
    #        python3 retention.py --now 2026-11-01
    #    应该报出一大堆活动追踪记录。报 0 就说明策略没接上。
    ap.add_argument("--now", metavar="YYYY-MM-DD",
                    help="假装今天是这一天（只用于验证；和 --apply 一起用会被拒绝）")
    a = ap.parse_args()

    now = None
    if a.now:
        if a.apply:
            # 拿假时间真删 = 一条命令删掉所有东西。不给这个机会。
            print("🔴 --now 只能和干跑一起用。拿假时间真删太容易一把清空。")
            return 1
        now = datetime.fromisoformat(a.now)

    print(f"=== 保留策略 {'执行' if a.apply else '干跑'} "
          f"{now or datetime.now():%Y-%m-%d %H:%M}"
          f"{'（假装的）' if now else ''} ===")
    r = run(POLICIES, apply=a.apply, now=now)

    print()
    if a.apply:
        print(f"删了 {r['deleted']} 条，查了 {r['checked']} 个库")
    else:
        print(f"会删 {r['deleted']} 条，查了 {r['checked']} 个库")
        print("（干跑，一个字节都没写。加 --apply 才真删）")

    if r["refused"]:
        # 🔴 非零退出 —— systemd 单元会变 failed，体检那条会红。
        #    熔断了却悄悄成功退出，等于没有熔断。
        print(f"🔴 熔断了：{'、'.join(r['refused'])} —— 一条都没删，去查判据")
        return 1
    if r["checked"] == 0:
        return 1
    return 1 if r["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
