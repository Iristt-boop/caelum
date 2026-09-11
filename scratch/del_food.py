import sqlite3

db = 'D:/claude-code/data/nox-bridge.db'
con = sqlite3.connect(db)
cur = con.cursor()

# 先看 food 表里有没有「减脂烤箱板」
rows = cur.execute("SELECT id, name, cal_100g, protein_100g, carbs_100g, fat_100g FROM foods WHERE name LIKE '%减脂烤箱板%'").fetchall()
print("找到的记录:", rows)

if rows:
    for r in rows:
        fid = r[0]
        # 删除它
        cur.execute("DELETE FROM foods WHERE id=?", (fid,))
        print(f"已删除 id={fid}  {r[1]}")
    con.commit()
    # 确认删干净
    left = cur.execute("SELECT id, name FROM foods WHERE name LIKE '%减脂烤箱板%'").fetchall()
    print("剩余:", left)
else:
    print("没找到，可能不在这张表")

con.close()
