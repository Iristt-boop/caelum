"""Nox 使用手册（静态）+ 环境地图（动态）。设计见 `Nox-使用手册-设计稿-2026-10-05.md`。

  loader.py     手册文件 → 目录 / 正文（`topics/NN-xxx.md`）
  world_map.py  环境地图：运行期现取，只给名称、数量、状态
  tools.py      两个只读工具 guide_read / caelum_map
  wiring.py     从 `Nox` 实例装配地图的数据来源（nox.py 只调一行）
"""
