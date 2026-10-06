"""想起开心的旧事 —— 「想逗她」的第二个来源（V4.5「促狭的机会机制」，她 2026-10-06 拍板）。

## 为什么要有它

原来的促狭只有一个来源：**她正在闹**（playfulness.py，最近 20 分钟的气氛）。
可他主动找她的时候她多半不在聊天，所以那一刻促狭永远是 0 ——
09-29→10-06 他主动开口 185 次，心情里「想逗她」**一次都没有**。

设计稿里她给的方向：

> 人的快乐很多时候不是检测来的：看到猫视频、想起你喜欢的东西、翻到老照片、聊到一个梗。
> 第五帖该是「突然看到你之前发的小猫照片，想起你当时开心的样子」。

## 她定的三件事（10-06）

- **来源**：记忆库里开心的记忆 + Gallery 收藏的照片
- **只换心情，不加次数**：他本来就要想起她的那一次里，多一个可以抽中的心情；每天 8 次上限不变
- 快涨快散：同一件旧事 14 天内不再拿出来

## 第三路：一起做过的事（10-06 晚，她：「先做 1 和 2」）

设计稿（CAELUM-RESONANCE-ARCHITECTURE V4.5）里促狭该接的是 Memory + Curiosity +
**Relationship Memory**。话题池那路不接：池子里是 HN / GitHub / 论文，是「他被勾着」的料，
不是逗她的料。共活动接上了 —— 一起听过的歌、一起看过的片、一起在读的书：

    事实   World Model 里 SharedActivitiesSource 早就在记（listening_together / watching_session /
           reading_progress，retention 永久保留）
    细节   各回原服务取：歌 → eryu 歌曲记忆里他当时点歌的理由；片 → bridge 票根上她的短评；
           书 → 共读里她的划线和批注。取不到细节就只说事实，**取不到不抛**

## 怎么挑：先挑哪一路，再在那一路里挑

记忆库 43 条、收藏照片几张、共活动十来件 —— 全摊在一起按权重抽，几乎永远是记忆库。
所以先在**有货的几路**里均匀挑一路，再在那一路里按开心程度抽。

## 分寸

她不舒服的时候不逗（调用方看担心，见 `service._think_of_her`）。
这里只负责「有没有一件开心的旧事可以想起」，不判断该不该说。
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from attention.dream import _as_list, _created_of, _is_locked, _load_buckets
from temporal import CST

logger = logging.getLogger(__name__)

#: 多开心才算（记忆桶的 valence）。0.8 以上 3~60 天前的约 74 条（10-06 线上）
MIN_VALENCE = 0.8
#: 「之前」的意思：太新的是「刚才」，不是「想起」；太老的已经远了
MIN_AGE = timedelta(days=3)
MAX_AGE = timedelta(days=60)
#: 生活里的事才算。修代码、上班的开心不是她想被想起的那种
WARM_DOMAINS = frozenset({"恋爱", "友谊", "影视", "游戏", "饮食", "宠物", "日常", "手工", "社交",
                          "家庭", "出行", "居家", "情绪", "阅读", "内心", "梦境", "睡眠"})
COLD_DOMAINS = frozenset({"编程", "工作"})
#: 同一件旧事多久内不再拿出来
REUSE_GAP = timedelta(days=14)
STATE_KEY = "fond.used"

_BARE_ID = re.compile(r"^[0-9a-f]{8,}$")


@dataclass(frozen=True)
class Fond:
    """一件可以想起来的开心旧事。"""

    kind: str          # memory / photo / listen / watch / read
    key: str           # 去重用：桶 id / 照片 id / 歌 id / 观影场次 / 书 id
    title: str         # 短名，进 because
    detail: str        # 给他看的那段
    age_days: int
    #: 照片：给 send_gallery_image 的 query（按描述匹配）；歌：给 listen_play 的参数
    query: str = ""


#: 哪一路。先在有货的几路里均匀挑一路（见模块说明「怎么挑」）
_FAMILY = {"memory": "memory", "photo": "photo", "listen": "shared", "watch": "shared", "read": "shared"}

_VIDEO_EXT = re.compile(r"\.(mkv|mp4|avi|mov|rmvb|flv|wmv|m4v|ts)$", re.I)
_BRACKETS = re.compile(r"\s*[（(\[【][^）)\]】]*[）)\]】]\s*")
#: 文件名里年份 / 分辨率 / 字幕说明之后的都是发布组的标签
_RELEASE_TAIL = re.compile(r"[.\s_-]+((19|20)\d{2}|\d{3,4}p|中英|双语|BluRay|WEB).*$", re.I)


def _clean_title(raw: str) -> str:
    """观影记录的标题是文件名（「The.Sheep.Detectives.2026.1080p中英字幕.mp4」）—— 洗成人话。"""
    s = _VIDEO_EXT.sub("", str(raw or "").strip())
    s = _BRACKETS.sub(" ", s)
    s = _RELEASE_TAIL.sub("", s)
    if "." in s and " " not in s:
        s = s.replace(".", " ")
    return s.strip() or str(raw or "").strip()


def _book_title(raw: str) -> str:
    """共读的书 id 是「大问题_简明哲学导论」这种 —— 下划线是副标题的冒号。"""
    return str(raw or "").replace("_", "：").strip()


class FondSource:
    """从记忆库和 Gallery 收藏里挑一件开心的旧事。

    `buckets_dir` 读记忆桶（同 dream.py 的读法）；`bridge` 读 Gallery 收藏；
    `store` 记哪些最近用过（source_state）。哪一路读不到就只用另一路，**读挂了不抛**。
    """

    def __init__(self, *, store: Any, buckets_dir: str | None, bridge: Any = None,
                 world: Any = None, eryu: Any = None, reading: Any = None,
                 rng: Any = None) -> None:
        self.store, self.buckets_dir, self.bridge = store, buckets_dir, bridge
        #: 共活动：world 给事实，eryu / reading / bridge 给细节。哪个是 None 那块就少一点
        self.world, self.eryu, self.reading = world, eryu, reading
        self.rng = rng or random

    # ------------------------------------------------------------ 候选

    def _memories(self, now: datetime) -> list[tuple[float, Fond]]:
        if not self.buckets_dir:
            return []
        try:
            buckets = _load_buckets(Path(self.buckets_dir))
        except Exception:  # noqa: BLE001
            logger.warning("读记忆桶失败，这次只从照片里想", exc_info=True)
            return []
        out = []
        for b in buckets:
            m = b["meta"]
            try:
                valence = float(m.get("valence"))
            except (TypeError, ValueError):
                continue
            created = _created_of(m)
            name = str(m.get("name") or "").strip()
            domains = set(_as_list(m.get("domain")))
            if (valence < MIN_VALENCE or not created or not (MIN_AGE <= now - created <= MAX_AGE)
                    or _is_locked(m) or str(m.get("type", "")).lower() in ("feel", "archive", "archived")
                    or not name or _BARE_ID.match(name)
                    or not (domains & WARM_DOMAINS) or (domains & COLD_DOMAINS)):
                continue
            out.append((valence, Fond(kind="memory", key=str(m.get("id")), title=name,
                                      detail=f"{name}：{b['body'][:200]}",
                                      age_days=(now - created).days)))
        return out

    def _photos(self, now: datetime) -> list[tuple[float, Fond]]:
        if self.bridge is None:
            return []
        r = self.bridge.get("/api/gallery/list?filter=favorites")
        if not getattr(r, "ok", False):
            logger.warning("读 Gallery 收藏失败（%s），这次只从记忆里想", getattr(r, "error", "?"))
            return []
        data = getattr(r, "data", None)
        items = data.get("items") if isinstance(data, dict) else data
        out = []
        for p in items or []:
            desc = str(p.get("description") or "").strip()
            try:
                created = datetime.fromisoformat(str(p.get("created_at")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=CST)   # 没带时区的按她那边算（同 dream._created_of）
            if not desc or now - created < MIN_AGE:
                continue
            out.append((0.9, Fond(kind="photo", key=str(p.get("id")), title=f"那张照片（{desc[:16]}）",
                                  detail=f"她收藏的一张照片：{desc[:200]}", age_days=(now - created).days,
                                  query=desc[:12])))
        return out

    def _facts(self, type_: str, now: datetime) -> list[tuple[datetime, dict]]:
        """World Model 里这类共活动，3~60 天前的（每条带时间）。"""
        try:
            evs = self.world.query(type_, days=MAX_AGE.days, limit=200, now=now)
        except Exception:  # noqa: BLE001
            logger.warning("读共活动（%s）失败，这类这次不想", type_, exc_info=True)
            return []
        return [(e.observed_at, e.raw or {}) for e in evs
                if MIN_AGE <= now - e.observed_at <= MAX_AGE]

    def _get(self, client: Any, path: str, params: dict | None = None) -> Any:
        """取细节。取不到返回 None —— 细节是锦上添花，没有就只说事实。"""
        if client is None:
            return None
        try:
            r = client.get(path, params) if params else client.get(path)
        except Exception:  # noqa: BLE001
            logger.warning("取共活动细节失败：%s", path, exc_info=True)
            return None
        if not getattr(r, "ok", False):
            logger.warning("取共活动细节失败：%s（%s）", path, getattr(r, "error", "?"))
            return None
        return getattr(r, "data", None)

    def _shared(self, now: datetime) -> list[tuple[float, Fond]]:
        if self.world is None:
            return []
        out: list[tuple[float, Fond]] = []

        # 一起听的歌：每首取最近一次
        songs: dict[str, tuple[datetime, dict]] = {}
        for at, o in self._facts("listening_together", now):
            sid = str(o.get("song_id") or "")
            if sid and (sid not in songs or at > songs[sid][0]):
                songs[sid] = (at, o)
        if songs:
            mem = (self._get(self.eryu, "/music/memory") or {})
            mem = mem.get("memories") if isinstance(mem, dict) else None
            mem = mem if isinstance(mem, dict) else {}
            for sid, (at, o) in songs.items():
                name, artist = str(o.get("title") or sid), str(o.get("artist") or "")
                e = mem.get(sid) or {}
                why = str(e.get("notes") or e.get("feeling") or "").strip()
                n = int(e.get("togetherCount") or o.get("together_count") or 1)
                detail = f"你们一起听过《{name}》" + (f"（{artist}）" if artist else "") + f"，一起听了 {n} 次。"
                if why:
                    detail += f"你当时放给她的理由：{why[:120]}"
                out.append((1.0, Fond(kind="listen", key=sid, title=f"一起听的《{name}》", detail=detail,
                                      age_days=(now - at).days,
                                      query=f"song_id={sid}, name={name}, artist={artist}")))

        # 一起看的片：一场一件；有票根用票根上的片名和她的短评
        shows: dict[str, tuple[datetime, dict]] = {}
        for at, o in self._facts("watching_session", now):
            k = str(o.get("session_id") or "")
            if k and k not in shows:
                shows[k] = (at, o)
        if shows:
            tk = self._get(self.bridge, "/api/tickets")
            items = tk.get("items") if isinstance(tk, dict) else None
            tickets = {str(t.get("session_id")): t for t in items or [] if isinstance(t, dict)}
            for k, (at, o) in shows.items():
                t = tickets.get(k) or {}
                title = str(t.get("title") or "").strip() or _clean_title(o.get("title"))
                mins = o.get("minutes")
                detail = f"你们一起看过《{title}》" + (f"（看了 {mins} 分钟{'，看完了' if o.get('finished') else ''}）"
                                                    if mins else "") + "。"
                if str(t.get("review") or "").strip():
                    detail += f"她的票根短评：「{str(t['review']).strip()[:80]}」"
                out.append((1.0, Fond(kind="watch", key=k, title=f"一起看的《{title}》", detail=detail,
                                      age_days=(now - at).days)))

        # 一起读的书：每本取最近一次进度；细节挑一条她的划线批注
        books: dict[str, tuple[datetime, dict]] = {}
        for at, o in self._facts("reading_progress", now):
            b = str(o.get("book_id") or "")
            if b and (b not in books or at > books[b][0]):
                books[b] = (at, o)
        for b, (at, o) in books.items():
            title = _book_title(o.get("title") or b)
            detail = f"你们一起在读《{title}》" + (f"（读到 {o['progress']}%）" if o.get("progress") else "") + "。"
            anns = self._get(self.reading, "/api/annotations", {"bookId": b})
            hers = [a for a in anns or [] if isinstance(a, dict) and not a.get("parentId")
                    and (a.get("author") or "user") == "user" and (a.get("quote") or a.get("note"))]
            if hers:
                a = self.rng.choice(hers)
                if a.get("quote"):
                    detail += f"她在书上划过「{str(a['quote'])[:60]}」"
                if a.get("note"):
                    detail += f"，写了：「{str(a['note'])[:80]}」"
            out.append((1.0, Fond(kind="read", key=b, title=f"一起读的《{title}》", detail=detail,
                                  age_days=(now - at).days)))
        return out

    # ------------------------------------------------------------ 挑 / 记

    def _used(self) -> dict[str, str]:
        try:
            return dict((self.store.get_source_state(STATE_KEY) or {}).get("items") or {})
        except Exception:  # noqa: BLE001
            return {}

    def pick(self, now: datetime) -> Fond | None:
        """挑一件。先在有货的几路里均匀挑一路，再在那一路里按开心程度加权随机；
        14 天内用过的不挑。没有就 None。"""
        used = self._used()
        families: dict[str, list[tuple[float, Fond]]] = {}
        for w, f in self._memories(now) + self._photos(now) + self._shared(now):
            last = used.get(f"{f.kind}:{f.key}")
            if last and now - datetime.fromisoformat(last) < REUSE_GAP:
                continue
            families.setdefault(_FAMILY.get(f.kind, f.kind), []).append((w, f))
        if not families:
            return None
        fresh = families[self.rng.choice(sorted(families))]
        total = sum(w for w, _ in fresh)
        r = self.rng.uniform(0, total)
        acc = 0.0
        for w, f in fresh:
            acc += w
            if r <= acc:
                return f
        return fresh[-1][1]

    def mark_used(self, fond: Fond, now: datetime) -> None:
        """他真的从这件事说起了才记（[SKIP] 掉的不算）。"""
        used = self._used()
        used[f"{fond.kind}:{fond.key}"] = now.isoformat()
        cutoff = now - REUSE_GAP
        used = {k: v for k, v in used.items() if datetime.fromisoformat(v) >= cutoff}
        try:
            self.store.set_source_state(STATE_KEY, {"items": used})
        except Exception:  # noqa: BLE001
            logger.warning("「想起过哪件旧事」没存住，可能两周内会再想起一次", exc_info=True)
