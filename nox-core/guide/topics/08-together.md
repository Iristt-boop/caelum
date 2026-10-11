---
topic: together
title: 共读、共影、共听
ask: 她问「共读 / 共影 / 共听是什么逻辑」「你怎么知道我在看什么」时翻
---
三件「一起做」的事，各有一个小服务，你经工具和它们打交道：
- **共读**：一个独立的读书服务（co-reading）。她划线写批注，服务把批注转给你回一句，回在页边；进度、批注、书签卡片都存在那边，你用 `reading_*` 工具读和写。
- **共影**：她在 Caelum 里放片子，换场或她暂停时，画面会被描述成文字（常带字幕）发给你；你看到的是这段描述，不是视频本身。一集看完会整理成「这集讲了什么」，下次看同一部剧时你会带着前情。
- **共听**：一个自托管的播放器（eryu）接着网易云，你用 `listen_*` / `eryu_*` 工具搜歌、放歌、记听歌笔记。
<!-- 源：co-reading/src/nox-client.js；bridge/server.js 共影段；nox-core/memory/watched.py；nox-core/tools/eryu.py -->

你此刻是不是在共影 / 共读里，看每轮的【你在哪】，不用翻这篇。
细节会改，**要讲具体就去看代码**：`co-reading/`、`bridge/server.js` 里 watch 相关的段、`nox-core/memory/watched.py`、`nox-core/tools/eryu.py`。
做完看：讲到某次一起看 / 读 / 听的具体内容时，出处是这一轮的工具返回或【前情】，不是凭印象。
