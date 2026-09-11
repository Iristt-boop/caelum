import sqlite3

con = sqlite3.connect('D:/claude-code/nox-app/backend/data/nox.db')
cur = con.cursor()

# 列出所有表
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("TABLES:", tables)

# 找包含 food 的表
for t in tables:
    if 'food' in t.lower():
        print("\n=== TABLE:", t, "===")
        cols = [c[1] for c in cur.execute(f"PRAGMA table_info({t})")]
        print("COLUMNS:", cols)
        # 看前几行
        rows = cur.execute(f"SELECT * FROM {t} LIMIT 20").fetchall()
        for r in rows:
            print(r)

con.close()
