"""Caelum 配置层（设计：Caelum-配置层-设计稿-2026-10-05.md）。

  schema.py   表结构（🔴 不存任何 key / token）
  store.py    SettingsStore：settings.db 的读写
  seed.py     种子：从 BACKENDS / config.models / NOX_* 环境变量灌库（行为零变化）
  shadow.py   影子对比：从库里重建配置，与现状逐字段比；密钥只比摘要
  startup.py  启动钩子（api/server.py 调一行；永远不许拖垮启动）

P0（本期）：只灌、只对账，**库不被读取、不生效**。P1 起前端只读视图；P2 起才有写入。
"""
