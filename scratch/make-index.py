"""把素材包的单图拼成带编号的索引图，方便挑件。

用法：python make-index.py <目录> <输出png> [每格放大倍数] [列数] [起始序号] [数量]
编号取文件名里的数字 —— 后面挑好了直接按编号取文件。
"""
import glob
import os
import re
import sys

from PIL import Image, ImageDraw

src, out = sys.argv[1], sys.argv[2]
S = int(sys.argv[3]) if len(sys.argv) > 3 else 4
cols = int(sys.argv[4]) if len(sys.argv) > 4 else 14
start = int(sys.argv[5]) if len(sys.argv) > 5 else 0
count = int(sys.argv[6]) if len(sys.argv) > 6 else 10**9


def num(path):
    m = re.findall(r"\d+", os.path.basename(path))
    return int(m[0]) if m else 0


files = sorted(glob.glob(os.path.join(src, "*.png")), key=num)[start:start + count]
ims = [(Image.open(f).convert("RGBA"), num(f)) for f in files]
cw = max(i.width for i, _ in ims) * S + 10
ch = max(i.height for i, _ in ims) * S + 24
rows = (len(ims) + cols - 1) // cols
sheet = Image.new("RGBA", (cols * cw + 8, rows * ch + 8), (250, 247, 242, 255))
d = ImageDraw.Draw(sheet)
for n, (im, idx) in enumerate(ims):
    big = im.resize((im.width * S, im.height * S), Image.NEAREST)
    x, y = (n % cols) * cw + 6, (n // cols) * ch + 4
    d.text((x, y), str(idx), fill=(150, 60, 60, 255))
    sheet.paste(big, (x, y + 14), big)
sheet.save(out)
print(f"{out} {sheet.size} 共 {len(ims)} 张（编号 {num(files[0])}~{num(files[-1])}）")
