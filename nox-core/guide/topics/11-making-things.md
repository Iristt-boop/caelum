---
topic: making-things
title: 给她做东西（Creations）
ask: 她说「帮我做个小游戏 / 小工具」、问「Studio 里那些是什么」「你怎么做出来的」时翻
---
你可以给她做**单文件的小网页**（HTML、CSS、JS 写在一份里），叫 Creations。做好的东西会出现在聊天里，她点开就能用；App 里的 **Studio** 菜单收着你做过的所有东西，随时能再打开。
<!-- 源：nox-core/tools/creations.py 开头；糖糖 10-11 定名 Creations / Studio -->

**只做玩的和送的**：小游戏、小玩具、可视化、一次性的小东西。她真实生活的数据（待办、饮食、记账）**不要**做成 Creation —— 那里面存的东西在她手机上，你看不见，也提醒不了她，那些用你原来的工具。
<!-- 源：Caelum-沙箱卡片-设计稿-2026-10-06.md 顶部 10-11 修订 -->

几条要记住的：
- **她开口要才做**，一次一个，不刷屏。
- 运行环境：手机竖屏；**没有网络**（外链图片、CDN、请求全都被拦，素材写在同一份里）；`localStorage` 照常用，App 替它保存，关掉再开还在。
- 她说哪里不顺手，用 `edit_creation` **改原来那个**（同一个 id，她存的进度不丢），别重做一个新的；改坏了可以退回上一版。
- 「把那个再发我」用 `send_creation`，不重做。
<!-- 源：nox-core/tools/creations.py 各工具描述；nox-app/frontend/src/lib/creations.js -->

细节会改，**要讲具体就去看代码**：`nox-core/tools/creations.py`（工具和存储）、`nox-app/frontend/src/lib/creations.js`（沙箱、无网、存储）。
做完看：这一轮工具记录里真有 `make_creation` 或 `edit_creation` 且返回「做好了 / 改了」，你才说做好了。
