---
topic: models-and-routes
title: 模型与线路
ask: 她提到换模型、识图不对、为什么变贵或变慢时翻
---
**线路按用途分，每个用途指向一家厂商的一个型号。** 现在指向谁以 `caelum_map`（scope 填 `models`）为准，不要凭记忆答 —— 她随时会在页面上换。

- **Primary（聊天）**：和她聊天的那一条。服务器上只存一份，手机和电脑共用；她选了，下一句就换。提示词缓存要一直热着，别劝她来回换。
- **Utility（杂活）**：意图分类、总结、记忆整理、话题池、每日卡片这些，全部共用一条。她换了，所有杂活下一次调用一起换。
- **Vision（看图）**：主线读不了图时，先让这条把图看成文字再交给主线。聊天、相册描述、电脑截屏、Stack-chan 拍照用的都是它。
- **Video（看视频）/ Image（画图）**：能选能存，还没有功能在用。
- **声音**：语音合成和识别走 bridge，各有自己的线路，暂时不在这几条里。
<!-- 源：nox-core/settings_store/slots.py 文件头；nox-core/nox.py chat_model_key / vision_cfg；配置层设计稿第二十九节 -->

**谁来换、在哪换**：只有她，在 Caelum OS 的 Settings → Models。页面从上到下是：
- **Now**：现在聊天用的是哪个。
- **Providers**：有哪几家。点开一家，可以换 key、测一下、加型号；右上角「加一家」。
- **Slots**：每个用途一个下拉框，选了立刻生效，旁边有「换回去」。
- **Usage**：用量和费用。

你改不了配置，但能**提议**：`propose_config_change` 在聊天里发一张卡（换某个用途的型号、在某家名下加型号、请她给某家填 key），她点「同意」才生效。她自己想换，就告诉她去这一页的哪一块。
<!-- 源：nox-app/caelum-os-ui/src/pages/SetModels.jsx；配置层设计稿第二十八节 -->

**几条容易答错的事实**：
- **能不能看图看的是型号，不是厂商。** 例如 deepseek-flash 能看，deepseek-v4-pro 实测看不见（收了图不报错，悄悄丢掉）。以档案里的能力为准，没测过的型号默认只能聊天。
- **key 从哪来**：她在页面上填的优先于服务器上配的。页面上任何地方都不显示 key，你也别复述、别向她要。
- **新加的厂商和型号**都要真测通过才能保存：真测是发一句很短的话，回得出字才算通，缓存命中和看图另外列。代码自带的那几家，名字和地址改不了。
<!-- 源：nox-core/settings_store/seed.py VERIFIED_VISION；settings_store/keys.py；settings_store/edit.py；settings_store/probe.py -->

**为什么 Primary 不该中途换**：提示词缓存按「模型 + 前缀字节」匹配，换一次模型整段前缀按原价重算。对话历史也只往后追加、不在中间改写，同样是为了不打碎缓存。
<!-- 源：nox-core/nox.py adapter_for 的注释；nox-core/personality/prompt.py 开头 -->

**各家缓存的原理**（存多久各家不同；打法记在每个型号的档案里）：
- **自动前缀**（DeepSeek、智谱、OpenAI 系）：没有参数，按整条请求的开头匹配，所以每轮会变的内容放最后。智谱只留一到三分钟。
- **打断点**（Anthropic，以及 OpenRouter 上的 Claude / Qwen）：在请求里标出哪段可以缓存。
<!-- 源：nox-core/agent/adapters.py；配置层设计稿第二十二节 -->

**她问费用**：去 Models 页的 Usage 和账本看。账是两本：一本按用量估算，一本是厂商那边的真实余额差值，不要混成一个数。Usage 的按型号表是从开始到现在的累计，不是只算正在聊天的那一个。
<!-- 源：PROJECT.md 第四十二节 42.1 / 42.2；SetModels.jsx Usage 段 -->
做完看：回答里每个数字、每个型号名都来自 `caelum_map` 或这几页，查不到就说查不到。
