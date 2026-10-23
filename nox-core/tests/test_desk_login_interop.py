"""她手机上的加密（shared/desk/login-crypto.js，WebCrypto）和我电脑上的解密（desk-agent/desk_login.py）是同一套。

两边各写一份，最怕的就是「各自测都过、合在一起解不开」—— 这里真跑一遍：Node（和浏览器同一套 WebCrypto）加密 → Python 解。
也守：拿去别的卡 / 改一个字节都解不开。没装 node 就跳过（她电脑上是装着的）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

from tests.test_desk import _desk_login  # noqa: E402

desk_login = _desk_login()
JS_PATH = ROOT / "nox-app" / "shared" / "desk" / "login-crypto.js"
JS = JS_PATH.as_uri()
PW = "PW-密码-SENTINEL-ü€"


def _seal_in_node(card: dict, values: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("没装 node")
    if not JS_PATH.is_file():
        pytest.skip("nox-app 不在旁边（只检出了外层仓库，比如暂存区快照）")
    script = (f"import {{ sealLogin }} from {json.dumps(JS)};"
              f"const out = await sealLogin({json.dumps(card)}, {json.dumps(values, ensure_ascii=False)});"
              "process.stdout.write(JSON.stringify(out));")
    r = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _card():
    return desk_login.new_card("https://login.taobao.com/x", None,
                               [{"key": "user", "kind": "user", "label": ""}, {"key": "pass", "kind": "pass", "label": ""}])


def test_红线_浏览器加密_我电脑解得开_值一字不差():
    c = _card()
    sealed = _seal_in_node({"id": c.login_id, "public_key": c.public_spki}, {"user": "tangtang", "pass": PW})
    assert PW not in json.dumps(sealed, ensure_ascii=False)
    assert desk_login.decrypt(c, sealed["epk"], sealed["iv"], sealed["ct"]) == {"user": "tangtang", "pass": PW}


def test_红线_拿去别的卡解不开_改一个字节也解不开():
    a, b = _card(), _card()
    sealed = _seal_in_node({"id": a.login_id, "public_key": a.public_spki}, {"pass": PW})
    with pytest.raises(ValueError):
        desk_login.decrypt(b, sealed["epk"], sealed["iv"], sealed["ct"])
    import base64
    ct = bytearray(base64.b64decode(sealed["ct"]))
    ct[0] ^= 1
    with pytest.raises(ValueError):
        desk_login.decrypt(a, sealed["epk"], sealed["iv"], base64.b64encode(bytes(ct)).decode())


def test_卡上没有的字段不收():
    c = _card()
    sealed = _seal_in_node({"id": c.login_id, "public_key": c.public_spki}, {"pass": PW, "evil": "x"})
    assert desk_login.decrypt(c, sealed["epk"], sealed["iv"], sealed["ct"]) == {"pass": PW}
