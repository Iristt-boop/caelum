import json, urllib.request, urllib.parse, os, re, time, random, subprocess, sys

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
OUT = "/root/data/library-covers"
LOG = "/root/library-covers.log"

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def token():
    out = subprocess.check_output(["systemctl", "show", "bridge", "-p", "Environment"], text=True)
    for part in out.split():
        if part.startswith("NOX_TOKEN="):
            return part.split("=", 1)[1]
    raise RuntimeError("no token")

def fetch(url, referer=None, timeout=12):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        **({"Referer": referer} if referer else {}),
    })
    return urllib.request.urlopen(req, timeout=timeout).read()

def clean(title):
    return re.sub(r"[（(【\[].*?[)）\]】]", "", title).strip()

def strip_author_tag(a):
    return re.sub(r"^\[[^\]]*\]\s*", "", a or "").strip()

def author_match(hers, cand_author):
    ca = cand_author or ""
    if not hers or len(hers) < 2:
        return True
    head = hers[:2]
    return head in ca or ca[:2] in hers

def suggest(q):
    url = "https://book.douban.com/j/subject_suggest?q=" + urllib.parse.quote(q)
    data = fetch(url, "https://book.douban.com/")
    return json.loads(data)

def main():
    token_val = token()
    req = urllib.request.Request("http://127.0.0.1:3003/api/library/books",
                                 headers={"X-Nox-Token": token_val})
    items = json.load(urllib.request.urlopen(req, timeout=15))["items"]
    todo = [b for b in items if not os.path.exists(os.path.join(OUT, b["id"] + ".jpg"))]
    log(f"total {len(items)}, todo {len(todo)}")

    ok = miss = 0
    consec_empty = 0
    missed = []
    for idx, b in enumerate(todo):
        q = clean(b["title"])
        picked = None
        try:
            data = suggest(q)
            cands = [c for c in data if c.get("type") == "b" and c.get("pic")]
            hers = strip_author_tag(b["author"])
            for c in cands:
                if author_match(hers, c.get("author_name")):
                    picked = c
                    break
            if cands and not picked:
                # 有候选但作者都对不上 —— 认第一个，聊胜于无的比例很小，宁可错放过
                miss_note = "author-mismatch"
            else:
                miss_note = "empty"
        except Exception as e:
            log(f"[{idx+1}/{len(todo)}] ERR {q}: {e}")
            missed.append(b)
            time.sleep(random.uniform(8, 15))
            continue

        if not picked:
            consec_empty += 1
            miss += 1
            missed.append(b)
            log(f"[{idx+1}/{len(todo)}] MISS {q} ({miss_note})")
            # 连续空 = 大概率被限流，冷却再走
            if consec_empty >= 3:
                cool = 60 + consec_empty * 15
                log(f"  throttle? cooling {cool}s")
                time.sleep(cool)
            else:
                time.sleep(random.uniform(4, 7))
            continue

        consec_empty = 0
        big = picked["pic"].replace("/s/public/", "/l/public/")
        try:
            img = fetch(big, "https://book.douban.com/")
            with open(os.path.join(OUT, b["id"] + ".jpg"), "wb") as f:
                f.write(img)
            ok += 1
            if idx % 10 == 0 or idx == len(todo) - 1:
                log(f"[{idx+1}/{len(todo)}] OK {q} (cum ok {ok} miss {miss})")
        except Exception as e:
            miss += 1
            missed.append(b)
            log(f"[{idx+1}/{len(todo)}] MISS(img) {q}: {e}")

        time.sleep(random.uniform(2.5, 4.5))

    # 第二遍：失败的重试一次，间隔放宽
    log(f"pass2 retry {len(missed)} missed")
    retried = 0
    for b in missed:
        time.sleep(random.uniform(20, 30))
        q = clean(b["title"])
        try:
            data = suggest(q)
            cands = [c for c in data if c.get("type") == "b" and c.get("pic")]
            if not cands:
                continue
            big = cands[0]["pic"].replace("/s/public/", "/l/public/")
            img = fetch(big, "https://book.douban.com/")
            with open(os.path.join(OUT, b["id"] + ".jpg"), "wb") as f:
                f.write(img)
            retried += 1
            ok += 1
            miss -= 1
            log(f"  retry OK {q}")
        except Exception as e:
            log(f"  retry MISS {q}: {e}")

    have = len(os.listdir(OUT))
    log(f"ALL DONE: covers now {have}, pass1 ok {ok} miss {miss}, pass2 recovered {retried}")

main()
