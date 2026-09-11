"""挑出「纯地板砖」——四边都没有深色描边的那些。

带黑边的砖是给房间边缘用的（墙脚、拐角），平铺到屋子中间会画出一格格的黑线。
用程序判而不是用眼睛挑：156 张里肉眼分不清哪块的边是深的。
"""
import glob
import os
import re

from PIL import Image

SRC = "D:/WorkBuddy/2026-09-02-17-20-41/pixel-assets/sierrassets_pack/floors and walls/individual sprites"


def num(p):
    return int(re.findall(r"\d+", os.path.basename(p))[0])


def edge_is_dark(im):
    """四条边上有没有明显偏暗的像素（描边）。"""
    px = im.convert("RGB").load()
    w, h = im.size
    edge = []
    for x in range(w):
        edge += [px[x, 0], px[x, h - 1]]
    for y in range(h):
        edge += [px[0, y], px[w - 1, y]]
    dark = sum(1 for r, g, b in edge if (r + g + b) / 3 < 90)
    return dark > len(edge) * 0.25


plain = []
for f in sorted(glob.glob(SRC + "/*.png"), key=num):
    im = Image.open(f)
    if im.size != (8, 8):
        continue
    if not edge_is_dark(im):
        rgb = im.convert("RGB")
        avg = tuple(sum(c) // (8 * 8) for c in zip(*rgb.getdata()))
        plain.append((num(f), avg))

print(f"纯地板砖 {len(plain)} 张：")
for n, avg in plain:
    warm = avg[0] > avg[2] + 20          # 偏暖 = 木色系
    print(f"  {n:3d}  平均色 rgb{avg}{'  ← 木色' if warm else ''}")
