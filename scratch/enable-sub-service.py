#!/usr/bin/env python3
"""Enable 3x-ui subscription service: set client subId, sub settings, all loopback-only."""
import json, sqlite3, subprocess

DB = "/etc/x-ui/x-ui.db"
SID = subprocess.run(["openssl", "rand", "-hex", "8"], capture_output=True, text=True).stdout.strip()
TOK_LINK = subprocess.run(["openssl", "rand", "-hex", "8"], capture_output=True, text=True).stdout.strip()
TOK_CLASH = subprocess.run(["openssl", "rand", "-hex", "8"], capture_output=True, text=True).stdout.strip()

con = sqlite3.connect(DB)
cur = con.cursor()

# 1. set subId on the client, both in inbounds.settings JSON and clients table
settings_json = cur.execute("SELECT settings FROM inbounds WHERE id=1").fetchone()[0]
s = json.loads(settings_json)
s["clients"][0]["subId"] = SID
cur.execute("UPDATE inbounds SET settings=? WHERE id=1", (json.dumps(s),))
cur.execute("UPDATE clients SET sub_id=? WHERE email='tangtang-main'", (SID,))

# 2. subscription service settings: enabled, loopback-only, two random token paths
rows = [
    ("subEnable", "true"),
    ("subListen", "127.0.0.1"),
    ("subPort", "2096"),
    ("subPath", "/%s/" % TOK_LINK),
    ("subClashEnable", "true"),
    ("subClashPath", "/%s/" % TOK_CLASH),
    ("subJsonEnable", "false"),
]
for k, v in rows:
    cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
con.commit()

print("sub_id   =", SID)
print("link_path=/%s/" % TOK_LINK)
print("clash_path=/%s/" % TOK_CLASH)
for k in ("subEnable", "subListen", "subPort", "subPath", "subClashEnable", "subClashPath"):
    print(k, "=", cur.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()[0])
print("client:", cur.execute("SELECT email, sub_id, enable FROM clients").fetchall())
con.close()
