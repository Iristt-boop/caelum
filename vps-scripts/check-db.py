import sqlite3, json
db = sqlite3.connect("/root/ombre-brain/buckets/dehydration_cache.db")
cur = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("Tables:", tables)
for t in tables:
    try:
        cur2 = db.execute(f"SELECT * FROM [{t}] LIMIT 1")
        cols = [d[0] for d in cur2.description]
        print(f"{t} cols:", cols)
    except Exception as e:
        print(f"{t}: {e}")
db.close()
