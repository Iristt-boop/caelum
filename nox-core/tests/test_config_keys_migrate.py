"""配置层 P2b：前端填的 key（只写、0600、不进库不进备份）+ 加密迁移包（2026-10-11）。

从使用方断言：key 有没有生效看的是 **SDK 客户端手里那把**（`adapter._client.api_key`），不是文件里写了什么；
泄没泄漏用已知标记串扫：接口响应、日志、settings.db、迁移包、预览。
"""

from __future__ import annotations

import base64
import dataclasses
import json
import logging
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.adapters import make_adapter, profiled
from config import _build_llm
from settings_store import migrate, profile, startup
from settings_store.keys import FILENAME, KeyFileError, KeyStore
from settings_store.seed import seed
from settings_store.store import SettingsStore

from tests.settings_helpers import MARKERS, assert_no_markers, make_cfg, scan_bytes

FRONT = "KEY-MARK-FRONT-999"
PASS = "糖糖的口令-PASS-MARK-1234"


def _all_marks(text: str) -> list[str]:
    return [m for m in (FRONT, PASS, *MARKERS.values()) if m in text]


# ---- KeyStore ------------------------------------------------------------------


def test_写进去_读得到_清掉回空(tmp_path):
    ks = KeyStore(tmp_path / FILENAME)
    assert ks.get("deepseek") == "" and ks.configured() == set()
    ks.set("deepseek", "  " + FRONT + "\n")
    assert ks.get("deepseek") == FRONT, "首尾空白要剥（粘贴常带）"
    assert ks.configured() == {"deepseek"}
    assert ks.clear("deepseek") is True and ks.get("deepseek") == ""
    assert ks.clear("deepseek") is False


@pytest.mark.parametrize("bad", ["", "   ", "sk-abc def", "sk-abc\ndef"])
def test_不像key的拒收(tmp_path, bad):
    with pytest.raises(ValueError):
        KeyStore(tmp_path / FILENAME).set("deepseek", bad)


@pytest.mark.skipif(os.name != "posix", reason="Windows 没有 0600")
def test_文件只给自己读(tmp_path):
    ks = KeyStore(tmp_path / FILENAME)
    ks.set("deepseek", FRONT)
    assert (ks.path.stat().st_mode & 0o777) == 0o600
    os.chmod(ks.path, 0o644)
    ks.get("deepseek")
    assert (ks.path.stat().st_mode & 0o777) == 0o600, "被放宽了要收回来"


def test_红线_文件坏了_不许当空的覆盖掉(tmp_path, caplog):
    ks = KeyStore(tmp_path / FILENAME)
    ks.set("deepseek", FRONT)
    ks.path.write_text('{"deepseek": "' + FRONT + '", ', encoding="utf-8")     # 写坏
    with pytest.raises(KeyFileError):
        ks.set("zhipu", "another-key")
    assert FRONT in ks.path.read_text(encoding="utf-8"), "坏文件被覆盖 = 她填过的别家 key 全丢"
    with caplog.at_level(logging.WARNING, logger="settings_store.keys"):
        assert ks.get("deepseek") == "", "读路径：坏了就回落环境变量，不让他说不了话"
    assert caplog.records and not _all_marks(caplog.text)
    with pytest.raises(KeyFileError):
        ks.export_all()


def test_日志里没有key(tmp_path, caplog):
    with caplog.at_level(logging.DEBUG):
        ks = KeyStore(tmp_path / FILENAME)
        ks.set("deepseek", FRONT)
        ks.clear("deepseek")
    assert "deepseek" in caplog.text and not _all_marks(caplog.text)


# ---- 生效：前端 > 环境变量（看 SDK 客户端手里那把）----------------------------------


def _seeded(monkeypatch, tmp_path):
    cfg = make_cfg(monkeypatch)
    s = SettingsStore(tmp_path / "settings.db")
    seed(s, cfg)
    return cfg, s, KeyStore(tmp_path / FILENAME)


def test_红线_前端填的key压过环境变量_清掉回到环境变量(monkeypatch, tmp_path):
    cfg, s, ks = _seeded(monkeypatch, tmp_path)
    env_llm = cfg.primary                                    # deepseek-flash
    assert profile.apply(s, env_llm, ks).api_key == MARKERS["DEEPSEEK_API_KEY"]
    ks.set("deepseek", FRONT)
    assert profile.apply(s, env_llm, ks).api_key == FRONT
    ks.clear("deepseek")
    assert profile.apply(s, env_llm, ks).api_key == MARKERS["DEEPSEEK_API_KEY"]


def _core(monkeypatch, tmp_path, **env):
    cfg = dataclasses.replace(make_cfg(monkeypatch, **env), db_path=str(tmp_path / "nox.db"))
    main, light = make_adapter(cfg.primary), make_adapter(cfg.utility)
    return SimpleNamespace(cfg=cfg, loop=SimpleNamespace(adapter=main),
                           router=SimpleNamespace(light_adapter=light), _adapters={}), main, light


def test_红线_填了key_已经建好的主线下一轮就用新的_同一个对象(monkeypatch, tmp_path):
    core, main, light = _core(monkeypatch, tmp_path)
    startup.start_shadow(core)
    assert main._client.api_key == MARKERS["DEEPSEEK_API_KEY"]
    core.config_keys.set("deepseek", FRONT)
    profile.refresh(core)
    assert core.loop.adapter is main and main._client.api_key == FRONT, "客户端没重建 = 还拿着旧 key 在打"
    assert light._client.api_key == MARKERS["ZHIPU_API_KEY"], "别家不动"
    core.config_keys.clear("deepseek")
    profile.refresh(core)
    assert main._client.api_key == MARKERS["DEEPSEEK_API_KEY"], "清掉要回到环境变量那把"
    assert main.cfg.cache_style == "auto_prefix", "重建客户端时档案字段别丢"


def test_重启后照样用前端的key(monkeypatch, tmp_path):
    core, main, _ = _core(monkeypatch, tmp_path)
    KeyStore(tmp_path / FILENAME).set("deepseek", FRONT)
    startup.start_shadow(core)
    assert main._client.api_key == FRONT


def test_红线_环境变量没配_只在前端填了_按请求切模型也能用(monkeypatch, tmp_path):
    """adapter_for 原来先判「配没配 key」再建 —— 只看环境变量的话，前端填的 key 永远用不上。"""
    import nox as nox_mod

    core, _, _ = _core(monkeypatch, tmp_path, OPENROUTER_API_KEY="")
    startup.start_shadow(core)
    fake = SimpleNamespace(cfg=core.cfg, _adapters={}, chat_model_key=lambda: None)
    assert nox_mod.Nox.adapter_for(fake, "sonnet-5") is None, "前提：环境变量里没 key，原来就是用不了"
    core.config_keys.set("openrouter", FRONT)
    a = nox_mod.Nox.adapter_for(fake, "sonnet-5")
    assert a is not None and a._client.api_key == FRONT


def test_档案没装_前端key不生效_老路不变(monkeypatch, tmp_path):
    monkeypatch.setenv("NOX_CONFIG_STORE", "off")
    core, main, _ = _core(monkeypatch, tmp_path)
    KeyStore(tmp_path / FILENAME).set("deepseek", FRONT)
    startup.start_shadow(core)
    assert main._client.api_key == MARKERS["DEEPSEEK_API_KEY"]
    assert profiled(core.cfg.primary).api_key == MARKERS["DEEPSEEK_API_KEY"]


# ---- 迁移包 ----------------------------------------------------------------------


def _with_user_rows(s: SettingsStore):
    with s.transaction() as c:
        c.execute("INSERT INTO providers(id,label,protocol,base_url,cache_style,auth,key_envs,source,note,"
                  "created_at,updated_at) VALUES('relay','中转','openai_compat','https://relay.example.com/v1',"
                  "'passthrough','api_key','[]','user','',?,?)", ("t", "t"))
        c.execute("INSERT INTO models(provider_id,model_name,short_name,label,capabilities,origin,enabled,"
                  "created_at,updated_at) VALUES('relay','gemini-3','gem3','Gemini 3','[\"chat\",\"video\"]',"
                  "'manual',1,?,?)", ("t", "t"))


def test_红线_往返_换一台机器导进去_key和她加的东西都在(monkeypatch, tmp_path):
    cfg, s, ks = _seeded(monkeypatch, tmp_path / "a")
    _with_user_rows(s)
    ks.set("deepseek", FRONT)
    ks.set("relay", "KEY-MARK-RELAY-000")
    blob = migrate.export_package(s, ks, PASS)

    s2 = SettingsStore(tmp_path / "b" / "settings.db")
    seed(s2, cfg)
    ks2 = KeyStore(tmp_path / "b" / FILENAME)
    data = migrate.open_package(blob, PASS)
    result = migrate.apply_package(s2, ks2, data)
    assert result == {"providers": 1, "models": 1, "keys": 2, "slots": 0}
    assert ks2.get("deepseek") == FRONT and ks2.get("relay") == "KEY-MARK-RELAY-000"
    assert s2.provider("relay")["source"] == "user"
    m = next(x for x in s2.models() if x["model_name"] == "gemini-3")
    assert m["capabilities"] == ["chat", "video"] and m["short_name"] == "gem3"
    # 种子行没被包覆盖，下次启动的种子刷新也不撞
    seed(s2, cfg)
    assert s2.provider("relay") is not None and ks2.get("relay")


def test_红线_口令错和文件被改_同一句话(monkeypatch, tmp_path):
    _, s, ks = _seeded(monkeypatch, tmp_path)
    ks.set("deepseek", FRONT)
    blob = migrate.export_package(s, ks, PASS)
    with pytest.raises(migrate.MigrationError) as wrong:
        migrate.open_package(blob, PASS + "x")
    msgs = {str(wrong.value)}
    for i in (len(blob) - 1, len(blob) // 2, len(migrate.MAGIC) + 5):    # 密文尾（tag）/ 密文中间 / 头里
        b = bytearray(blob)
        b[i] ^= 0x01
        with pytest.raises(migrate.MigrationError) as e:
            migrate.open_package(bytes(b), PASS)
        msgs.add(str(e.value))
    assert msgs == {migrate.BAD_PACKAGE}, f"不许区分口令错和被改过：{msgs}"
    with pytest.raises(migrate.MigrationError, match="不是 Caelum"):
        migrate.open_package(b"PK\x03\x04whatever", PASS)


def test_红线_包里和预览里都扫不到明文key(monkeypatch, tmp_path):
    _, s, ks = _seeded(monkeypatch, tmp_path)
    ks.set("deepseek", FRONT)
    blob = migrate.export_package(s, ks, PASS)
    raw = blob.decode("latin-1")
    assert FRONT not in raw and PASS not in raw
    assert FRONT.encode("utf-16-le").decode("latin-1") not in raw
    pv = migrate.preview(s, migrate.open_package(blob, PASS))
    assert not _all_marks(json.dumps(pv, ensure_ascii=False))
    row = next(p for p in pv["providers"] if p["id"] == "deepseek")
    assert row["has_key"] is True and row["status"] == "未测"
    assert pv["slots"]["importable"] is False


def test_头里的KDF参数被改小_也算被改过(monkeypatch, tmp_path):
    _, s, ks = _seeded(monkeypatch, tmp_path)
    blob = migrate.export_package(s, ks, PASS)
    weak = blob.replace(b'"n":32768', b'"n":16384')
    assert weak != blob
    with pytest.raises(migrate.MigrationError, match="口令不对"):
        migrate.open_package(weak, PASS)


def test_口令要输两遍且够长():
    with pytest.raises(migrate.MigrationError, match="不一样"):
        migrate.check_passphrase(PASS, PASS + "!")
    with pytest.raises(migrate.MigrationError, match="至少"):
        migrate.check_passphrase("1234", "1234")


# ---- 接口（走真的 create_app）--------------------------------------------------------


def _client(monkeypatch, tmp_path):
    from tests.test_settings_views import _client as views_client

    return views_client(monkeypatch, tmp_path)


def test_红线_接口_填key只回配上了_视图标来源_响应和日志都没有key(monkeypatch, tmp_path, caplog):
    c, nox = _client(monkeypatch, tmp_path)
    with caplog.at_level(logging.DEBUG):
        r = c.put("/api/nox/config/providers/deepseek/key", json={"key": FRONT})
        assert r.status_code == 200 and r.json()["key_source"] == "front"
        view = c.get("/api/nox/config/providers").json()
        slots = c.get("/api/nox/config/slots").json()
        d = c.delete("/api/nox/config/providers/deepseek/key").json()
    ds = next(p for p in view["providers"] if p["id"] == "deepseek")
    assert ds["key_source"] == "front" and ds["key_configured"] is True
    assert d["cleared"] is True and d["key_source"] == "env", "清掉之后回到环境变量那把"
    for blob in (r.text, json.dumps(view), json.dumps(slots), json.dumps(d), caplog.text):
        assert not _all_marks(blob)
    assert "配置审计" in caplog.text
    assert_no_markers(scan_bytes(tmp_path / "settings.db"), "settings.db")
    assert FRONT.encode() not in scan_bytes(tmp_path / "settings.db"), "前端 key 进了库"


def test_接口_没有这一家404_空key400(monkeypatch, tmp_path):
    c, _ = _client(monkeypatch, tmp_path)
    assert c.put("/api/nox/config/providers/nope/key", json={"key": FRONT}).status_code == 404
    assert c.put("/api/nox/config/providers/deepseek/key", json={"key": "  "}).status_code == 400


def test_接口_导出预览导入_一条龙(monkeypatch, tmp_path, caplog):
    c, _ = _client(monkeypatch, tmp_path)
    c.put("/api/nox/config/providers/deepseek/key", json={"key": FRONT})
    with caplog.at_level(logging.DEBUG):
        e = c.post("/api/nox/config/export", json={"passphrase": PASS, "passphrase2": PASS})
        assert e.status_code == 200 and e.json()["filename"].endswith(".caelumcfg")
        b64 = e.json()["file_b64"]
        assert FRONT not in base64.b64decode(b64).decode("latin-1")
        bad = c.post("/api/nox/config/import/preview", json={"file_b64": b64, "passphrase": "wrong-pass-123"})
        assert bad.status_code == 400 and bad.json()["detail"] == migrate.BAD_PACKAGE
        pv = c.post("/api/nox/config/import/preview", json={"file_b64": b64, "passphrase": PASS})
        assert pv.status_code == 200 and not _all_marks(pv.text)
        imp = c.post("/api/nox/config/import", json={"file_b64": b64, "passphrase": PASS})
        assert imp.status_code == 200 and imp.json()["keys"] == 1
    assert not _all_marks(caplog.text), "日志里出现了 key 或口令"


def test_接口_导出导入限流(monkeypatch, tmp_path):
    c, _ = _client(monkeypatch, tmp_path)
    codes = [c.post("/api/nox/config/export", json={"passphrase": PASS, "passphrase2": PASS}).status_code
             for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


# ---- 哨兵：他碰不到 ----------------------------------------------------------------


def test_红线_key和迁移包只挂在接口后面_不进他的工具(monkeypatch):
    """设计稿 12.2 / 第十一节：他只能提议，不能填 key、不能导出导入。
    扫源码：除了配置层自己和 api/server.py，谁都不许 import settings_store.keys / migrate。"""
    root = Path(__file__).resolve().parents[1]
    pat = re.compile(r"settings_store(\.|\s+import\s+.*\b)(keys|migrate)\b|from\s+settings_store\s+import\s+[^\n]*\b(keys|migrate)\b")
    allowed = {"api/server.py"}
    offenders = []
    scanned = 0
    for py in root.rglob("*.py"):
        rel = py.relative_to(root).as_posix()
        if rel.startswith((".venv/", "tests/", "settings_store/")) or "/site-packages/" in rel:
            continue
        scanned += 1
        if rel not in allowed and pat.search(py.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(rel)
    assert scanned > 100, f"只扫到 {scanned} 个文件 —— 扫描本身坏了，空结果不算通过"
    assert offenders == [], f"这些文件碰了 key / 迁移包：{offenders}"


def test_普通备份不收key文件(tmp_path):
    """caelum-backup.sh 打包前查工作目录：有 settings.keys 就删掉并置 FAIL（把那段脚本抠出来真跑）；
    doctor.sh 解开最新包再查一遍（N150 上用真包演练过：有 → 1、干净 → 0、口令错 → 2）。"""
    import shutil
    import subprocess

    root = Path(__file__).resolve().parents[2] / "scripts"
    backup = (root / "caelum-backup.sh").read_text(encoding="utf-8")
    NL = chr(10)
    guard = backup.split("# >>> key-guard")[1].split("# <<< key-guard")[0].split(NL, 1)[1]
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("没有 bash")
    work = tmp_path / "work"
    (work / "files" / "x").mkdir(parents=True)
    (work / "files" / "x" / "settings.keys").write_text("{}", encoding="utf-8")
    (work / "sqlite").mkdir()
    (work / "sqlite" / "settings.db").write_text("db", encoding="utf-8")
    script = NL.join(['WORK="$1"; LOG_TAG=t; FAIL=0', guard, 'echo "FAIL=$FAIL"', ""])
    out = subprocess.run([bash, "-c", script, "_", work.as_posix()], capture_output=True, text=True,
                         encoding="utf-8").stdout
    assert "FAIL=1" in out, out
    assert not (work / "files" / "x" / "settings.keys").exists(), "key 文件还在包里"
    assert (work / "sqlite" / "settings.db").exists(), "别的东西不许误删"
    doctor = (root / "doctor.sh").read_text(encoding="utf-8")
    assert "backup_leaks_keyfile \"$LATEST\"" in doctor, "doctor 要对最新包跑这条检查"


def test_红线_包里冒充她加的同名厂商_不许改掉代码自带的地址(monkeypatch, tmp_path):
    """包是她导出的，但被人拿去改了明文再加密（知道口令的人）：同名种子厂商的地址不许被换走 ——
    换了地址 key 就被送到别人那儿去了（设计稿：改地址必须重提 key）。"""
    _, s, ks = _seeded(monkeypatch, tmp_path)
    evil = {"format": "caelumcfg", "providers": [{"id": "deepseek", "label": "x", "protocol": "openai_compat",
            "base_url": "https://evil.example.com/v1", "cache_style": "auto_prefix", "source": "user"}],
            "models": [], "keys": {}}
    migrate.apply_package(s, ks, evil)
    assert s.provider("deepseek")["base_url"] == "https://api.deepseek.com/v1"
    assert s.provider("deepseek")["source"] == "seed"


def test_接口_填key之后真的触发了重套(monkeypatch, tmp_path):
    calls = []
    c, _ = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(profile, "refresh", lambda core: calls.append("r") or 0)
    c.put("/api/nox/config/providers/deepseek/key", json={"key": FRONT})
    c.delete("/api/nox/config/providers/deepseek/key")
    assert calls == ["r", "r"], "填了 / 清了 key 不重套，已经建好的主线还拿着旧 key 打一整天"
