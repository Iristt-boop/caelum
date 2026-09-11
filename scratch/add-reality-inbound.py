#!/usr/bin/env python3
"""Add a VLESS + Reality inbound to 3x-ui v3.7 via panel API (localhost only)."""
import json, re, sys, urllib.request, urllib.parse, http.cookiejar

BASE = "http://127.0.0.1:41729/234afcf0c1de6d2cc9237599/"
IN_PORT = 56218
HDRS = {"X-Requested-With": "XMLHttpRequest"}


def read_kv(path):
    kv = {}
    for line in open(path):
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()
    return kv


creds = read_kv("/root/xui-credentials.txt")
rk_txt = open("/root/reality-keys.txt").read()
PRIV = re.search(r"^PrivateKey: (\S+)", rk_txt, re.M).group(1)
PUB = re.search(r"^Password \(PublicKey\): (\S+)", rk_txt, re.M).group(1)
rkv = read_kv("/root/reality-keys.txt")
UUID = rkv["uuid"]
SID = rkv["shortid"]
SUBID = rkv.get("subid", "")

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def get_csrf():
    r = opener.open(urllib.request.Request(BASE + "csrf-token", headers=HDRS), timeout=10)
    d = json.loads(r.read().decode())
    tok = d.get("obj") or ""
    print("CSRF:", r.status, "got token" if tok else d)
    return tok


def login(tok):
    data = urllib.parse.urlencode(
        {"username": creds["username"], "password": creds["password"]}
    ).encode()
    h = dict(HDRS, **{"X-CSRF-Token": tok})
    r = opener.open(urllib.request.Request(BASE + "login", data=data, headers=h), timeout=10)
    body = r.read().decode()
    print("LOGIN:", r.status, body[:120])
    return '"success":true' in body.replace(" ", "")


tok = get_csrf()
if not login(tok):
    print("LOGIN FAILED, abort")
    sys.exit(1)

# session may have rotated on login — refresh token with the new cookie
tok = get_csrf()
h = dict(HDRS, **{"X-CSRF-Token": tok, "Content-Type": "application/json"})

settings = {
    "clients": [
        {
            "id": UUID,
            "flow": "xtls-rprx-vision",
            "email": "tangtang-main",
            "limitIp": 0,
            "totalGB": 0,
            "expiryTime": 0,
            "enable": True,
            "tgId": 0,
            "subId": SUBID,
            "reset": 0,
        }
    ],
    "decryption": "none",
    "fallbacks": [],
}

stream = {
    "network": "tcp",
    "security": "reality",
    "externalProxy": [],
    "tcpSettings": {"acceptProxyProtocol": False, "header": {"type": "none"}},
    "realitySettings": {
        "show": False,
        "xver": 0,
        "dest": "www.apple.com:443",
        "serverNames": ["www.apple.com"],
        "privateKey": PRIV,
        "minClient": "",
        "maxClient": "",
        "maxTimediff": 0,
        "shortIds": [SID],
        "settings": {
            "publicKey": PUB,
            "fingerprint": "chrome",
            "serverName": "",
            "spiderX": "/",
        },
    },
    "duplicationSettings": {},
    "noise": [],
    "sockopt": {
        "tcpFastOpen": False,
        "tcpMptcp": False,
        "tcpNoDelay": False,
        "domainStrategy": "AsIs",
        "dialerProxy": "",
        "tcpKeepAliveIdle": 100,
        "mark": 0,
    },
}

sniffing = {
    "enabled": True,
    "destOverride": ["http", "tls", "quic"],
    "metadataOnly": False,
    "routeOnly": False,
    "destOverrideIncludesSni": False,
}

allocate = {"strategy": "always", "refresh": 5, "concurrency": -1}

inbound = {
    "up": 0,
    "down": 0,
    "total": 0,
    "remark": "tokyo-reality",
    "enable": True,
    "expiryTime": 0,
    "listen": "",
    "port": IN_PORT,
    "protocol": "vless",
    "settings": json.dumps(settings),
    "streamSettings": json.dumps(stream),
    "tag": "inbound-" + str(IN_PORT),
    "sniffing": json.dumps(sniffing),
    "allocate": json.dumps(allocate),
    "clientStats": [],
}

body = json.dumps(inbound).encode()
for add_ep in ["panel/inbound/add", "panel/api/inbounds/add"]:
    try:
        req = urllib.request.Request(BASE + add_ep, data=body, headers=h)
        r = opener.open(req, timeout=15)
        ab = r.read().decode()
        print("ADD", add_ep, "->", r.status, ab[:300].replace("\n", " "))
        if '"success":true' in ab.replace(" ", ""):
            sys.exit(0)
    except Exception as e:
        print("ADD", add_ep, "-> EXC", e)
print("all endpoints failed")
sys.exit(1)
