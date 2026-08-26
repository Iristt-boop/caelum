"""LocationProvider —— 她在哪、刚到家还是刚出门。

数据源按优先级排列：
  1. HA Tracker（person/device_tracker，WiFi 探知 home/not_home，可靠且不耗电）
  2. Caelum PWA（Geolocation → Bridge，GPS 精确但需要她主动点按钮）

两个源互补：HA 给 home/not_home 信号，PWA 给精确坐标。

## 动态 TTL

和别的 Provider 不同，这里的缓存时间取决于**结果的 tag**：
- home: 30 分钟（在家几小时不动）
- work: 20 分钟
- transit: 1 分钟（移动中要跟紧）
- leisure: 5 分钟
- unknown: 3 分钟（保守）

## 不进每轮名单

位置数据和 memory/home 同理：只在 Router 判断她问了相关的话时才加载。
日常闲聊每轮塞一段「在 xx 区」是白付常量文本的钱。

## 隐私

原始坐标 enrich 后丢弃（高德返回的 address/poi/aoi 才给模型看）。
Bridge 那边有 24h 自动清理，但 Core 这边不落盘。
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from context.base import BaseContextProvider, Turn
from tools.http import RestClient

logger = logging.getLogger(__name__)

# 动态 TTL：按状态标签决定缓存多久
_TTL_MAP: dict[str, timedelta] = {
    "home": timedelta(minutes=30),
    "work": timedelta(minutes=20),
    "transit": timedelta(minutes=1),
    "leisure": timedelta(minutes=5),
    "unknown": timedelta(minutes=3),
}
_DEFAULT_TTL = timedelta(minutes=3)


# ── HA Tracker Source ────────────────────────────────────────────

class HATrackerSource:
    """从 Home Assistant 的 person/device_tracker 实体拿位置。

    person 实体聚合了所有 device_tracker —— 只要有一个 tracker 在线
    （WiFi/GPS/蓝牙），person 就会更新。不需要手机装额外 App，
    WiFi 级别的 device_tracker（nmap/ping/路由器集成）就能判断在不在家。

    设好之后完全不耗电：HA 在路由器侧探知，不碰手机。
    """

    def __init__(self, api_url: str, token: str,
                 person_entity: str = "person.nox",
                 timeout: float = 6.0) -> None:
        self._url = api_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._person = person_entity
        self._timeout = timeout

    def fetch(self) -> dict[str, Any] | None:
        """查 person 实体。返回归一化的位置数据，不可用时返回 None。"""
        try:
            req = urllib.request.Request(
                f"{self._url}/api/states/{self._person}",
                headers=self._headers,
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))

        except Exception as exc:
            logger.debug("HA Tracker 查询失败: %s", exc)
            return None

        state = data.get("state", "unknown")
        attrs = data.get("attributes", {})
        lat = attrs.get("latitude")
        lng = attrs.get("longitude")

        # 三种情况：
        # 1. home — 回家了，最可靠（不需要 GPS）
        # 2. not_home + GPS — 在外面，手机开了位置共享
        # 3. not_home 无 GPS — 在外面但不知道在哪
        # 4. unknown — 没有 device_tracker 在跑（比如还没装 Companion App）

        if state == "unknown":
            logger.debug("HA person.nox 状态 unknown —— 没有 device_tracker 在运行")
            return None

        if state == "home":
            return {
                "source": "ha_tracker",
                "ha_state": "home",
                "latitude": lat,
                "longitude": lng,
                "trigger": "ha_home",
                "timestamp": data.get("last_changed", ""),
                "battery": None,
                "accuracy": None,
            }

        # not_home
        result: dict[str, Any] = {
            "source": "ha_tracker",
            "ha_state": "not_home",
            "latitude": lat,
            "longitude": lng,
            "trigger": "ha_away",
            "timestamp": data.get("last_changed", ""),
            "battery": None,
            "accuracy": None,
        }

        if lat is not None and lng is not None:
            result["has_coords"] = True
        else:
            result["has_coords"] = False

        return result


# ── Bridge / PWA Source ──────────────────────────────────────────

class BridgeLocationSource:
    """从 Bridge 读 PWA 上报的最新位置（GPS 坐标）。"""

    def __init__(self, bridge: RestClient) -> None:
        self._bridge = bridge

    def fetch(self) -> dict[str, Any] | None:
        """拉取 Bridge 缓存的最新位置。没有数据返回 None。"""
        r = self._bridge.get("/api/location/latest")
        if not r.ok:
            logger.debug("Bridge 位置查询失败: %s", r.error)
            return None

        loc = (r.data or {}).get("location")
        if not loc:
            return None

        lat = float(loc["lat"])
        lng = float(loc["lng"])
        return {
            "source": "caelum_pwa",
            "latitude": lat,
            "longitude": lng,
            "accuracy": float(loc.get("accuracy", 100)),
            "trigger": loc.get("trigger", "manual"),
            "timestamp": loc.get("timestamp", ""),
            "battery": loc.get("battery"),
        }


# ── Provider ─────────────────────────────────────────────────────

class LocationProvider(BaseContextProvider):
    """她在哪、刚到家还是刚出门。"""

    name = "location"
    section = "user"
    ttl = timedelta(minutes=3)  # 默认值；实际 TTL 由 _ttl_for() 动态决定

    def __init__(self, bridge_url: str = "", bridge_token: str = "",
                 gaode_key: str = "",
                 ha_api_url: str = "", ha_api_token: str = "",
                 **kw: Any) -> None:
        super().__init__(**kw)
        self._gaode_key = gaode_key

        # 构建数据源列表（按优先级）
        self._sources: list[tuple[str, Any]] = []

        # 1. HA Tracker（WiFi 探知，可靠且不耗电）
        if ha_api_url and ha_api_token:
            self._sources.append((
                "ha_tracker",
                HATrackerSource(ha_api_url, ha_api_token),
            ))

        # 2. PWA / Bridge（GPS，精确但需要主动上报）
        if bridge_url and bridge_token:
            bridge = RestClient(
                bridge_url,
                headers={"X-Nox-Token": bridge_token},
                timeout=8.0,
                auth_hint="bridge token 对不上",
            )
            self._sources.append((
                "caelum_pwa",
                BridgeLocationSource(bridge),
            ))

    # ---- 动态 TTL ----

    @staticmethod
    def _ttl_for(tag: str | None) -> timedelta:
        return _TTL_MAP.get(tag or "", _DEFAULT_TTL)

    def get_state(self, turn: Turn | None = None,
                  force_refresh: bool = False) -> dict[str, Any]:
        """覆盖基类：按结果 tag 动态决定缓存多久。

        基类的 get_state 用固定 ttl 写缓存，这里改成按 tag 算。
        其他逻辑（volatile 跳过、失败退 stale、异常兜底）保持不变。
        """
        turn = turn or Turn()
        if not force_refresh and not self.volatile:
            hit = self.cache.get(self.name)
            if hit is not None:
                return hit.value

        try:
            value = self._fetch(turn)
        except Exception as exc:  # noqa: BLE001
            stale = self.cache.get(self.name, allow_stale=True)
            if stale is not None:
                logger.warning(
                    "%s 拉取失败（%s: %s），退回 %.0f 秒前的旧数据",
                    self.name, type(exc).__name__, exc, stale.age_s,
                )
                return {**stale.value, "stale": True, "stale_age_s": stale.age_s}
            logger.warning(
                "%s 拉取失败且没有旧数据: %s: %s",
                self.name, type(exc).__name__, exc,
            )
            return {"available": False, "error": f"{type(exc).__name__}: {exc}"}

        if not isinstance(value, dict):
            raise TypeError(
                f"{self.name}._fetch() 要返回 dict，拿到的是 {type(value).__name__}"
            )

        # 按 tag 决定 TTL，不是固定值
        tag = value.get("place", {}).get("tag", "unknown")
        ttl = self._ttl_for(tag)
        self.cache.set(self.name, value, ttl)
        logger.debug("location tag=%s → ttl=%s", tag, ttl)
        return value

    # ---- 主逻辑 ----

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        """多源仲裁：依次尝试各数据源，第一个有结果的胜出。"""
        if not self._sources:
            raise RuntimeError("LocationProvider 没有任何数据源可用")

        for src_name, src in self._sources:
            raw = src.fetch()
            if raw is not None:
                source = raw.get("source", src_name)
                logger.debug("location 数据源=%s trigger=%s", source,
                             raw.get("trigger", ""))
                break
        else:
            raise RuntimeError(
                "所有数据源都没有位置数据"
                "（HA person 未知且 PWA 还没上报过）"
            )

        # 统一的后续处理流水线
        normalized = self._normalize(raw)
        enriched = self._enrich(normalized)
        tagged = self._infer_tag(enriched)
        return self._build_state(tagged)

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """把不同数据源的字段归一化。"""
        source = raw.get("source", "unknown")
        result: dict[str, Any] = {
            "raw_source": source,
            "trigger": raw.get("trigger", ""),
            "timestamp": raw.get("timestamp", ""),
        }

        # HA 专有字段（_infer_tag 需要用来判 home/not_home）
        ha_state = raw.get("ha_state", "")
        if ha_state:
            result["ha_state"] = ha_state

        # 坐标（可选）
        lat = raw.get("latitude")
        lng = raw.get("longitude")
        if lat is not None and lng is not None:
            result["latitude"] = float(lat)
            result["longitude"] = float(lng)

        # 精度（PWA 有，HA 没有）
        acc = raw.get("accuracy")
        if acc is not None:
            result["accuracy"] = float(acc)

        # 电量（PWA 有）
        bat = raw.get("battery")
        if bat is not None:
            result["battery"] = bat

        return result

    def _enrich(self, raw: dict[str, Any]) -> dict[str, Any]:
        """调高德逆地理编码，坐标 → 语义。失败不阻塞。"""
        if not self._gaode_key:
            return raw

        lng = raw.get("longitude")
        lat = raw.get("latitude")
        if not lng or not lat:
            return raw

        try:
            url = (
                "https://restapi.amap.com/v3/geocode/regeo"
                f"?location={lng},{lat}&key={self._gaode_key}"
                "&radius=1000&extensions=all"
            )
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if data.get("status") != "1":
                logger.warning("高德 API 返回非 1: %s", data.get("info", ""))
                return raw

            regeo = data.get("regeocode") or {}
            comp = regeo.get("addressComponent") or {}

            raw["address"] = regeo.get("formatted_address", "")
            aois = regeo.get("aois") or []
            raw["aoi"] = aois[0].get("name", "") if aois else ""
            pois = regeo.get("pois") or []
            raw["poi_list"] = [
                {"name": p.get("name", ""), "type": p.get("type", "")}
                for p in pois[:3]
            ]
            raw["district"] = comp.get("district", "")
            raw["city"] = comp.get("city") or comp.get("province", "")

        except Exception as exc:
            logger.warning("高德 enrich 失败: %s，用原始数据继续", exc)

        return raw

    def _infer_tag(self, enriched: dict[str, Any]) -> dict[str, Any]:
        """推断状态标签：home / work / transit / leisure / unknown。

        优先级：HA state > trigger > AOI 名 > POI 类型。
        """
        source = enriched.get("raw_source", "")
        trigger = enriched.get("trigger", "")
        ha_state = enriched.get("ha_state", "")
        aoi = str(enriched.get("aoi", ""))
        poi_list = enriched.get("poi_list") or []

        # ── HA 优先级最高：WiFi 级别的 home/not_home 最可靠 ──
        if source == "ha_tracker":
            if ha_state == "home":
                enriched["tag"] = "home"
                return enriched
            # HA 说 not_home —— 但不知道具体在哪
            # 有坐标时走下面的 AOI/POI 推断，没有就标 unknown
            if not enriched.get("latitude"):
                enriched["tag"] = "unknown"
                return enriched
            # 有坐标：继续走 AOI/POI 推断

        # ── trigger ──
        if trigger == "arrive_home":
            enriched["tag"] = "home"
        elif trigger == "leave_home":
            enriched["tag"] = "transit"

        # ── AOI 推断 ──
        elif any(k in aoi for k in (
            "小区", "住宅", "公寓", "花园", "嘉园", "花苑", "家园",
            "新村", "里", "苑", "邨",
        )):
            enriched["tag"] = "home"
        elif any(k in aoi for k in (
            "写字楼", "大厦", "园区", "中心", "广场", "科技",
            "软件", "创新", "创业", "产业园",
        )):
            enriched["tag"] = "work"

        # ── POI 推断 ──
        elif any(
            p.get("type", "") in (
                "购物服务", "餐饮服务", "体育休闲服务", "风景名胜",
                "咖啡厅", "茶馆", "电影院", "购物中心", "娱乐场所",
            )
            for p in poi_list
        ):
            enriched["tag"] = "leisure"
        elif any(
            p.get("type", "") in (
                "地铁站", "公交站", "火车站", "机场", "交通设施",
                "停车场", "长途汽车站",
            )
            for p in poi_list
        ):
            enriched["tag"] = "transit"

        else:
            enriched["tag"] = enriched.get("tag", "unknown")

        return enriched

    def _build_state(self, tagged: dict[str, Any]) -> dict[str, Any]:
        """构建标准化 Location State，丢弃原始坐标。"""
        tag = tagged.get("tag", "unknown")
        timestamp = tagged.get("timestamp", "")

        # 距离上次上报多久了
        stale_minutes = 0.0
        if timestamp:
            try:
                ts = datetime.fromisoformat(
                    timestamp.replace("Z", "+00:00")
                )
                stale_minutes = round(
                    (datetime.now().astimezone() - ts).total_seconds() / 60, 1
                )
            except Exception:
                pass

        place: dict[str, Any] = {
            "tag": tag,
            "name": tagged.get("aoi") or "",
            "district": tagged.get("district", ""),
            "city": tagged.get("city", ""),
        }
        # 附近 POI 也带出来，给模型用（"附近有什么"时他需要这些）
        poi_names = [
            p.get("name", "")
            for p in (tagged.get("poi_list") or [])
            if p.get("name")
        ]
        if poi_names:
            place["poi_nearby"] = poi_names

        return {
            "place": place,
            "movement": {
                "is_moving": tag == "transit",
                "last_trigger": tagged.get("trigger", ""),
                "time_since_last_report": stale_minutes,
            },
            "device": {
                "battery_level": tagged.get("battery"),
            },
            "_meta": {
                "source": tagged.get("raw_source", "unknown"),
                "available": True,
                "stale": False,
            },
        }

    # ---- 渲染 ----

    def render(self, state: dict[str, Any]) -> str:
        """把位置状态变成给他看的一句话。"""
        if state.get("available") is False:
            return ""

        stale = "（缓存）" if state.get("stale") else ""
        place = state.get("place", {})
        movement = state.get("movement", {})
        device = state.get("device", {})

        tag = place.get("tag", "unknown")
        city = place.get("city", "")
        district = place.get("district", "")
        name = place.get("name", "")
        area = name or district or city or "未知区域"

        # 附近 POI
        poi_nearby = place.get("poi_nearby") or []

        # 状态标签 → 自然语言
        tag_text_map = {
            "home": f"在家{stale}",
            "work": f"在公司（{area}）{stale}",
            "transit": f"在路上{stale}",
            "leisure": f"在外面（{area}）{stale}",
            "unknown": f"在 {area}{stale}",
        }
        tag_text = tag_text_map.get(tag, f"在 {area}{stale}")

        battery = device.get("battery_level")
        bat_text = f"，手机电量 {battery:.0f}%" if battery is not None else ""

        # 移动触发
        trigger = movement.get("last_trigger", "")
        trigger_text = ""
        if trigger == "arrive_home":
            trigger_text = "，刚到家"
        elif trigger == "leave_home":
            trigger_text = "，刚出门"

        # 数据来源标注（调试用，不给模型看）
        meta = state.get("_meta", {})
        source = meta.get("source", "")

        lines = [
            f"【位置】糖糖现在{tag_text}{trigger_text}{bat_text}。"
        ]

        if poi_nearby:
            lines.append(f"附近：{'、'.join(poi_nearby)}。")

        return "\n".join(lines)
