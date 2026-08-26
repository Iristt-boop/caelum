"""WeatherProvider 测试。不打网络。

重点守：
  1. **和风的错误藏在 body 的 code 里，HTTP 照样 200** —— 只看状态码会把
     「无效 key」当成拿到了数据（同 PROJECT.md 第二十节：200 ≠ 成功）
  2. 预报挂了不能拖垮整个 Provider —— 有实时的也能说话
  3. 下雨要单独说 —— 那是她真会据此做决定的信息
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context.base import Turn  # noqa: E402
from context.providers.weather import WeatherProvider  # noqa: E402
from tools.http import RestResult  # noqa: E402

NOW = {"code": "200", "now": {"text": "晴", "temp": "32", "feelsLike": "33",
                              "humidity": "61", "windDir": "南风", "windScale": "3"}}
DAILY = {"code": "200", "daily": [{"tempMax": "34", "tempMin": "24",
                                   "textDay": "晴", "precip": "0.0"}]}


def _p(now=NOW, daily=DAILY, fail_daily=False, http_fail=False):
    p = WeatherProvider(host="x.qweatherapi.com", key="k", location="101180101")

    def fake_get(path, params=None):
        if http_fail:
            return RestResult(False, error="HTTP 401: bad key")
        if "/3d" in path:
            if fail_daily:
                return RestResult(False, error="HTTP 500")
            return RestResult(True, daily)
        return RestResult(True, now)

    p.client.get = fake_get     # type: ignore[method-assign]
    return p


def test_parses_now_and_forecast():
    s = _p().get_state(Turn())
    assert s["text"] == "晴"
    assert s["temp"] == 32 and s["feels_like"] == 33
    assert s["temp_max"] == 34 and s["temp_min"] == 24
    assert s["has_forecast"] is True


def test_render_is_short_and_readable():
    p = _p()
    text = p.render(p.get_state(Turn()))
    assert text == "【外面】晴，32°，今天 24~34°，南风3级。"
    assert len(text) < 60


def test_feels_like_only_when_it_differs():
    """体感和实测差不到 2 度就别提，那是噪音。"""
    p = _p(now={"code": "200", "now": {"text": "阴", "temp": "20", "feelsLike": "21"}})
    assert "体感" not in p.render(p.get_state(Turn()))

    p2 = _p(now={"code": "200", "now": {"text": "闷热", "temp": "30", "feelsLike": "36"}})
    assert "体感 36°" in p2.render(p2.get_state(Turn()))


def test_rain_gets_its_own_sentence():
    """下雨是她真会据此做决定的信息，要单独说。"""
    p = _p(daily={"code": "200", "daily": [{"tempMax": "26", "tempMin": "20",
                                            "textDay": "中雨", "precip": "8.2"}]})
    text = p.render(p.get_state(Turn()))
    assert "带伞" in text and "中雨" in text


def test_no_rain_no_umbrella():
    assert "带伞" not in _p().render(_p().get_state(Turn()))


def test_qweather_error_code_is_not_success():
    """**这条最要紧**：和风把错误放在 body 的 code 里，HTTP 还是 200。
    只看状态码会把「无效 key」当成拿到了数据。"""
    s = _p(now={"code": "401", "now": {}}).get_state(Turn())
    assert s["available"] is False
    assert "code=401" in s["error"]


def test_forecast_failure_does_not_kill_the_provider():
    """预报挂了还有实时的，不能整个哑掉。"""
    s = _p(fail_daily=True).get_state(Turn())
    assert s.get("available") is not False
    assert s["text"] == "晴" and s["temp"] == 32
    assert s["has_forecast"] is False
    assert "32°" in _p(fail_daily=True).render(s)


def test_http_failure_is_reported():
    s = _p(http_fail=True).get_state(Turn())
    assert s["available"] is False
    assert "和风" in s["error"]


def test_caches_for_30min():
    """免费额度 50000/月，30 分钟 TTL 下上界约 2900/月，不用为省调用做妥协，
    但也没必要每轮真打。"""
    p = _p()
    calls = []
    orig = p.client.get

    def counting(path, params=None):
        calls.append(path)
        return orig(path, params)

    p.client.get = counting     # type: ignore[method-assign]
    for _ in range(5):
        p.get_state(Turn())
    assert len(calls) == 2, "30 分钟内只该打一次 now + 一次 3d"
    assert p.ttl == timedelta(minutes=30)
    assert p.volatile is False


def test_section_is_environment():
    assert _p().section == "environment"


def test_rest_client_handles_gzip():
    """和风**不管你要不要都返回 gzip**。直接 decode 会炸在 0x8b
    （gzip 魔数 1f 8b 的第二个字节），而报错信息完全指不到压缩上。
    没声明 gzip 的响应必须照常走 —— 别的调用方不能受影响。"""
    import gzip as _gzip
    import io
    import json as _json
    from unittest.mock import patch

    from tools.http import RestClient

    class FakeResp:
        def __init__(self, body, enc=None):
            self._b = body
            self.headers = {"Content-Encoding": enc} if enc else {}
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    payload = _json.dumps({"code": "200", "now": {"temp": "30"}}).encode()
    c = RestClient(base="https://x")

    with patch("urllib.request.urlopen", return_value=FakeResp(_gzip.compress(payload), "gzip")):
        r = c.get("/a")
        assert r.ok and r.data["now"]["temp"] == "30", "gzip 没解开"

    with patch("urllib.request.urlopen", return_value=FakeResp(payload)):
        r = c.get("/a")
        assert r.ok and r.data["code"] == "200", "没压缩的反而坏了"


def test_not_in_the_per_turn_lineup():
    src = (Path(__file__).resolve().parents[1] / "nox.py").read_text(encoding="utf-8")
    lineup = src.split('self.context.render(')[1].split(')')[0]
    assert '"weather"' not in lineup


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
