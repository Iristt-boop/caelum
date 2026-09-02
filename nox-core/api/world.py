"""World 页的数据源 —— **他此刻感知到的世界**，一次取齐。

界面是一张户型图：外面有天，房间里有设备，她在不在家、在忙什么。
`GET /api/nox/world` 就是它的后端。

## 🔴 四段各报各的，绝不合并成一个 ok

天气、设备、她在不在家、她在忙什么，是**四条完全独立的链路**。
HA 挂了不该把天空一起黑掉；感知层没连上也不影响灯亮着。
所以每段自带 `ok`/`error`，前端照着画：

    ok=False  →  那块留白 / 打问号，**不许画成「关着」或「晴天」**

这条是 §34「失败就说失败」的延续。**「空调关着」和「查不到空调」
在界面上必须长得不一样** —— 否则她看见的是一个自信的谎。

## 🔴 设备清单不在这里抄第二份

家居清单已经漂移过三处（PROJECT §20），这里**运行时问 ha-mcp 要**
（`hass_list_devices`，那是唯一真源），状态再打 HA 的 REST 拿结构化 JSON。

为什么不直接用 ha-mcp 的 `hass_snapshot`：它返回的是**给模型读的一段文本**
（`- 主卧 空调：off（室温 26°）`）。拿文本喂界面，等哪天有人改一句话的措辞
界面就碎了，而且碎得很安静。

⚠️ 也别顺手去改 ha-mcp —— **本地仓库那份比线上少两个设备**
（蒸蛋器和它的自动断电只在线上有，2026-09-02 查出来的）。
拿本地那份去部署会把蒸蛋器从线上抹掉。

## ⚠️ 离线的设备，HA 会保留断电前的最后状态

看着和正常的一模一样（§20 的老坑）。我们能拿到的诚实信号只有
`state == "unavailable" / "unknown"`，除此之外分不出「关着」和「拔了」。
所以这里**原样透传 state，不做任何美化**。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: 名字里没写房间的设备，在这儿补一句。
#: ⚠️ 这**不是**设备清单的副本 —— 清单永远从 ha-mcp 现取，
#: 这里只回答「这个名字属于哪一间」，有房间前缀的一律按前缀推。
_ROOM_HINT = {
    "蒸蛋器": "厨房",
    "蒸蛋器 自动断电": "厨房",
}

#: 透传给界面的设备属性。白名单而不是整包透传 ——
#: HA 的 attributes 里塞着几十个字段，多数是界面用不上的噪音。
_ATTRS = (
    "current_temperature",  # 空调：室温（不是每台都上报，主卧那台就没有）
    "temperature",          # 空调：设定温度
    "hvac_action",          # 空调：此刻在制冷还是在待机
    "fan_mode",
    "brightness",           # 灯：0-255
    "percentage",           # 风扇：档位
    "media_title",          # 电视：在放什么
)


def _room_of(name: str) -> tuple[str | None, str]:
    """从设备名推房间。`主卧 床头灯` → (`主卧`, `床头灯`)。

    没有空格的名字（`蒸蛋器`）查 `_ROOM_HINT`，还查不到就 room=None ——
    **宁可让它落在「其他」，也不要猜一个房间**：猜错了灯会亮在错的屋里。
    """
    # ⚠️ 提示表**优先于**前缀：`蒸蛋器 自动断电` 也有空格，
    # 按前缀推会得到「房间=蒸蛋器」这种不存在的屋子
    if name in _ROOM_HINT:
        room = _ROOM_HINT[name]
        label = name.split(" ", 1)[1] if " " in name else name
        return room, label
    if " " in name:
        room, label = name.split(" ", 1)
        return room, label
    return None, name


def parse_device_list(text: str) -> list[tuple[str, str]]:
    """解析 `hass_list_devices` 的清单：`- 主卧 风扇 → fan.xxx`。

    ⚠️ 认 `→` 也认 `->`：这行文案是给模型看的，改动它的人不会想到界面在读。
    解析不出来的行**跳过**，不猜。
    """
    out: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-").strip()
        for arrow in ("→", "->"):
            if arrow in line:
                name, _, eid = line.partition(arrow)
                name, eid = name.strip(), eid.strip()
                if name and "." in eid:
                    out.append((name, eid))
                break
    return out


def collect_sky(get_json: Callable[[str], dict]) -> dict:
    """外面什么天。和风 `/v7/weather/now` + `/v7/weather/3d`。

    `icon` 是和风的天气代码，**它才是画动效的依据** —— `text` 是给人读的
    中文（「多云」「小雨」），拿字符串去匹配「有没有雨」迟早匹配漏。
    """
    try:
        now = (get_json("/v7/weather/now") or {}).get("now", {}) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("天气拿不到: %s", exc)
        return {"ok": False, "error": f"{type(exc).__name__}"}

    today: dict[str, Any] = {}
    try:
        days = (get_json("/v7/weather/3d") or {}).get("daily", []) or []
        today = days[0] if days else {}
    except Exception as exc:  # noqa: BLE001
        # 预报挂了不致命：有实时的照样能画天空（同 WeatherProvider 的处理）
        logger.warning("拿不到今日预报（实时的还在）: %s", exc)

    def num(d: dict, k: str) -> float | None:
        v = d.get(k)
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    return {
        "ok": True,
        "text": now.get("text"),
        "icon": now.get("icon"),
        "temp": num(now, "temp"),
        "feels_like": num(now, "feelsLike"),
        "humidity": num(now, "humidity"),
        "precip": num(now, "precip"),
        "temp_max": num(today, "tempMax"),
        "temp_min": num(today, "tempMin"),
        "sunrise": today.get("sunrise"),
        "sunset": today.get("sunset"),
        "has_forecast": bool(today),
    }


def collect_home(list_devices: Callable[[], str],
                 state_of: Callable[[str], dict]) -> dict:
    """家里的设备。清单现问 ha-mcp，状态打 HA REST。"""
    try:
        text = list_devices()
    except Exception as exc:  # noqa: BLE001
        logger.warning("设备清单拿不到: %s", exc)
        return {"ok": False, "error": f"{type(exc).__name__}", "devices": []}

    pairs = parse_device_list(text)
    if not pairs:
        # 清单是空的 ≠ 家里没设备。如实说读不出来
        return {"ok": False, "error": "设备清单读不出来", "devices": []}

    def one(pair: tuple[str, str]) -> dict:
        name, eid = pair
        room, label = _room_of(name)
        base = {"name": name, "label": label, "room": room,
                "entity_id": eid, "domain": eid.split(".", 1)[0]}
        try:
            d = state_of(eid) or {}
        except Exception as exc:  # noqa: BLE001
            # 单个设备查不到，不能拖垮整张图
            return {**base, "ok": False, "state": None, "error": f"{type(exc).__name__}"}
        attrs = d.get("attributes") or {}
        return {
            **base,
            "ok": True,
            "state": d.get("state"),
            "changed_at": d.get("last_changed"),
            "attrs": {k: attrs[k] for k in _ATTRS if attrs.get(k) is not None},
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        devices = list(pool.map(one, pairs))

    return {"ok": True, "devices": devices}


def collect_presence(state_of: Callable[[str], dict],
                     person: str = "person.nox") -> dict:
    """她在不在家。HA 的 person 实体聚合了所有 device_tracker，

    跨地理围栏时官方 App 自己上报，**她不用点任何按钮**（见 presence.py）。
    """
    try:
        d = state_of(person) or {}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}"}
    st = d.get("state")
    if st in (None, "", "unknown", "unavailable"):
        return {"ok": False, "error": f"HA 说 {st or '空'}"}
    return {
        "ok": True,
        "home": st == "home",
        "state": st,          # home / not_home / 某个 zone 的名字
        "since": d.get("last_changed"),
    }


def collect_activity(current: tuple | None, link_ready: bool) -> dict:
    """她在忙什么 —— 感知层每分钟报一次的当前窗口（和躁动用的是同一份）。

    🔴 够不到她电脑的时候**只说够不到，不断言为什么**。
    2026-08-31 那次他说「电脑没开机」，而她电脑开着 ——
    真正的问题是本地网关没启动。**说错原因比说「不知道」更糟。**
    """
    if not link_ready:
        return {"ok": False, "error": "够不到她的电脑（分不出是网关没起、网不通，还是电脑关着）"}
    if not current:
        # 连着但没有窗口信息 = 不知道她在忙什么，**不是「她不忙」**
        return {"ok": True, "app": None, "seconds": 0}
    app = current[0] if len(current) > 0 else None
    seconds = current[1] if len(current) > 1 else 0
    return {"ok": True, "app": app, "seconds": seconds}


def ha_state_getter(api_url: str, token: str) -> Callable[[str], dict]:
    """打 HA REST 拿单个实体状态。Core 和 HA 同机，走 localhost。"""
    base = (api_url or "").rstrip("/")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def get(entity_id: str) -> dict:
        req = urllib.request.Request(f"{base}/api/states/{entity_id}", headers=headers)
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.loads(r.read().decode("utf-8"))

    return get


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()
