#!/usr/bin/env python3
"""Fix /api/messages session query: DESC then reverse, so LIMIT 500 keeps the NEWEST messages.
2026-08-14: main session has 508 rows; ASC LIMIT 500 hid 你猜猜/滴滴 and all replies after row 500.
"""
import io

P = "/root/bridge/server.js"
with io.open(P, encoding="utf-8") as f:
    src = f.read()

OLD = 'SELECT rowid AS id, id AS sessionId, role, content, timestamp, metadata FROM conversations WHERE id = ? ORDER BY rowid ASC LIMIT 500'
NEW = 'SELECT rowid AS id, id AS sessionId, role, content, timestamp, metadata FROM conversations WHERE id = ? ORDER BY rowid DESC LIMIT 500'

if OLD not in src:
    raise SystemExit("OLD query not found - aborting, no change made")

src = src.replace(OLD, NEW)

# also add the reverse() for the sid branch (it must be chronological ascending for the frontend)
OLD2 = '      [sid]\n    ));'
NEW2 = '      [sid]\n    ).reverse());'
# check how many occurrences
if src.count(OLD2) != 1:
    print("WARN: expected exactly 1 occurrence of the sid-branch close, got", src.count(OLD2))

src = src.replace(OLD2, NEW2)

with io.open(P, "w", encoding="utf-8") as f:
    f.write(src)

print("patched OK")
