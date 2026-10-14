# 云电脑 Personal Agent 调研 —— Muse / Grok Bot / Manus·Cue / OpenAI Dots

> 2026-10-14 · 为「给 Nox 配他自己的电脑（N150）」做的竞品调研。
> 四款都以**第三方技术拆解和实测**为主，官网口径只做对照；查不到的一律标「未能核实」。
> 结论服务于一个判断：Nox 不止陪伴，也能做生活上的 agent。

---

## TL;DR

1. 四款在 2026 年收敛成了同一个范式：**常驻个人 agent = 每人一台持久云电脑 + 长期记忆 + 主动触发 + 应用连接器 + 花钱要确认**。差别只在实现取舍。
2. **「浏览器为主、看屏幕兜底」就是全行业的实际做法**——嘴上都说"像人一样看屏幕动鼠标"，拆开看：Muse 实际读 accessibility 树、Manus 起家就是 Playwright 操作 DOM、OpenAI Operator 纯视觉路线被验证码卡死后 agent mode 转混合、Grok Bot 坚持视觉重放，结果"网站布局变了还点旧位置、而且不报错"被评测点名。像素路线只留给没有 DOM 的桌面软件（微信这类）。
3. 成本的分水岭在两处：**KV-cache 命中率**（Manus 官方：命中与否差 10 倍，输入输出比约 100:1）和**控制粒度**（一次 DOM 读写 vs 一轮截图视觉推理，差 1-2 个数量级——Aru 共玩一步 2.5 万 token 就是后者的账）。
4. 四家全踩了安全坑：Muse 上线前惊现 KVM 逃逸漏洞、Grok Bot 一台电脑全员共享登录态（官方承认不是安全边界）、OpenAI 夏天刚出过沙箱逃逸事件且个人版默认拿数据训练、网页注入在 Grok 聊天上实测约 40% 成功率。**自建反而是隐私最优解**——他的电脑就在家里，不经过任何大厂。
5. **咱们的工程能做**。N150 上开一台 Debian+轻桌面+Chromium 的 VM，装一份 Gateway；nox-core 侧加一只「他的电脑」的手，浏览器手走 Playwright/CDP，OS 屏幕小窗走 CDP screencast。现有零件几乎全能复用（LocalLink、长任务 runner、工具展示 v4、R8 确认制、embodied 闸门、stackchan 帧流），真正要新做的是三件：**VM 本体、浏览器工具面、屏幕小窗**。

---

## 一、四款产品逐个看

### 1. Muse —— Meta，2026-09-08 发布

**是什么**：Meta 的个人 AI agent，每名用户一台云端专属 VM（Muse Secure VM），替你发邮件、订餐厅、填表单、比价、砍账单。上线 13 天 250 万下载。底层自研 Muse Spark（1.3），10 月又发了更强的 Wonder 系列。

**机制拆解**（最有价值的一份：工程师 Rohan Adwankar 在自己 VM 里做的[完整拆解](https://rohanadwankar.github.io/posts/sandbox2.html)，[HN 讨论](https://news.ycombinator.com/item?id=49658976)）：

- 每用户独立 VM：Cloud Hypervisor（Rust VMM）里再套 systemd-nspawn 容器，2 vCPU / 7.7GB 内存，气球驱动回收空闲内存。
- **持久化做得最认真**：四块盘——只读根分区、加密可写层、校验镜像、**100GB LUKS2 加密持久卷**（家目录+Postgres）。升级 = 换新 VM 再"嫁接"持久卷，约 40 秒中断。
- **浏览器控制实际是 a11y 树，不是像素**：官方说"看屏幕动鼠标"，拆开发现驱动浏览器的 sub-agent 读的是 accessibility-tree 快照，禁脚本执行、禁 DevTools；浏览器本身按需租借（15 分钟租期的独立浏览器容器）。他还有一台无 cookie 的 headless chromium 干杂活。
- 工具约 **70 个**（spotify-api、instagram-cli、stripe-link……），每工具独立 uid；单文件 327MB Rust harness，不用 langchain/MCP；多模型路由（自研 + 外接 Claude/GPT）。
- **记忆 = VM 内 Postgres（194 张表，pgvector）+ Markdown 记忆文件**，本地嵌入 + reranker 混合检索；后台专模型做上下文压缩。
- **主动机制实锤**：拆解者在 VM 里发现 `workspace/cron.d/`（秒级到年度）、`goals/`、`HEARTBEAT.md`。
- **安全层 Sentinel**：host 侧独立审批方，出口走 MITM 代理 + eBPF + fake-IP DNS 防绕过；凭证代管（agent 拿 surrogate token，摸不到明文）。

**口碑**：消费级 UX 和安全架构公认最好（[NYT](https://www.nytimes.com/2026/09/22/technology/meta-muse-ai-agent.html)、[Lenny's](https://www.lennysnewsletter.com/p/muse-review-the-personal-ai-agent)）；HN 最高赞却是"Love the idea and the polish. Hate the owners."——**信任是大厂做个人 agent 的最大阻力**。实测翻车：把歇业多年的餐厅当推荐、结账仍需人工接管、频繁推销购买动作。上线前被 404 Media 披露过 KVM 逃逸漏洞（[报道](https://www.404media.co/meta-rushed-to-fix-muse-vm-escape-vulnerability-immediately-before-launch)）。

**价格**：免费档**每周 1 亿 tokens**；$20/月=5 亿/周；$100/月=30 亿/周（[帮助页](https://www.meta.com/help/subscriptions/1021145227643680/)）。大方的原因：商业模式是交易抽成，不是订阅费。

### 2. Grok Bot —— xAI，2026-08-11 发布（beta）

**是什么**：口号"一支 24/7 在线的 agent 团队，有自己的电脑"。底层 Grok 4.6（agentic RL 训练，含 computer use），无模型选择器。有趣的发现：基础设施实际跑在 **Cursor 的云上**（SpaceX 收购了 Anysphere）。

**机制拆解**（[rohitghumare: 共享电脑架构](https://rohitghumare.com/blog/grok-bot-shared-computer)最有价值）：

- 每账号一台**持久云端 Linux VM**（真浏览器+文件系统+终端），账号内最多 50 个 Bot **共用**这台电脑——"一个 Bot 本质是一段带 cookie 的会话 + 一个会点击的模型"。
- 控制是混合：OAuth 连接器（Gmail/Drive/Teams…）+ 无 API 网站用**视觉鼠标键盘重放**（不是 DOM 选择器）+ 自定义 MCP。
- **"演示即编程"（Routine）**：用户录屏演示一遍流程，系统编译成 步骤+参数+触发器，之后定时/事件触发自动跑。这是四家里最顺的"教他做事"交互。
- 文件、登录态、记忆跨会话持久；用户离线照常干活。

**口碑**：异步委托体验成立（HN 高赞："异步工作终于真的能用"，[讨论](https://news.ycombinator.com/item?id=49261514)）；邮件分诊类任务准确率 ~93% 但 7% 的草稿有事实错误（[eesel 实测](https://www.eesel.ai/blog/grok-bot-review)）。翻车点：**token 烧得极快**（"3 天烧掉 80% 周额度"，[Reddit](https://www.reddit.com/r/GrokBot/comments/1wlhfh4/how_do_you_reduce_grokbot_token_usage)）；视觉重放的可靠性问题——"布局变了还点旧位置且不报错"（[layer3labs 评测](https://www.layer3labs.io/guides/grok-bot-review)）；无审计日志。安全：共享登录态**官方承认不是安全边界**；注入测试 ~40% 成功率；xAI 条款对 agent 动作全面免责。

**价格**：随 SuperGrok $30/$100/$300 档下发（早期只绑 $300）。周额度独立、超额按 token 计费。

### 3. Manus 2.0 + Cue —— 2026-09-28 发布

**是什么**：通用 agent 的老牌（2025-03 爆红那位）。2.0 的 **Cue** 是独立的"个人智能体"应用，与 Manus 共用基建——**每个 agent 有独立数字身份：自己的邮箱、电话号码、钱包和专属电脑**，能发消息、在预算内付款、接电话留摘要、扫餐厅码代点餐排队；还能把多个 agent 拉群分工。（[官方博客](https://manus.im/blog/introducing-manus-2-0)、[TNW 报道](https://thenextweb.com/news/manus-2-0-cue-ai-agents-email-phone-wallet)）这是四家里**离"生活上的 agent"最近的一步**，也是和 AI 伴侣方向直接撞车的动作。

**机制**（Manus 是工程方法论输出最多的一家）：

- 每任务一个隔离云 VM；**休眠沙箱会被回收**（免费 7 天/Pro 21 天），登录取态由 Cloud Browser 跨任务记住；长驻引导到 2.0 新增的 Cloud Computer。
- 起家即被逆向：**Playwright + Docker 操作浏览器**，泄露的 system prompt 显示约 29 个工具（[renschni 拆解](https://gist.github.com/renschni/4fbc70b31bad8dd57f3370239dccd58f)）——"Claude + Qwen + 工具循环"。
- **KV-cache 工程**（[官方总结](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)，业界教科书）：输入输出比 ~100:1，缓存命中与否成本差 10 倍；手段=前缀稳定、append-only 上下文、mask logits 而非动态增删工具、todo.md 反复重读、**错误留在上下文里让它自我修正**、文件系统当"无限上下文"。典型任务 ~50 次工具调用。
- **Automations**：定时 + **事件触发**（新邮件、数据变化、日历）——"agent 主动找你"的产品形态，可以直接抄。
- 2025-12 曾被 Meta 收购又被撤销（政治原因），2026-10 完成超 5 亿美元新融资，估值 ~40 亿。

**口碑**：工作过程全透明可回放、Wide Research 批量独一档、MIT TR 实测单任务 ~$2（约 ChatGPT deep research 的 1/10）；骂点：更慢、像"聪明但走捷径的实习生"、积分烧得快且任务失败也扣。GAIA 自报 SOTA 后被实锤用过 dev set。

**价格**：一路降价——2025 年 $39/$199 → 2026-10 免费档 300 积分/天、**Pro 低至 $20/月**。降价背后就是 KV-cache 工程把成本打下来了。

### 4. OpenAI Dots —— 2026-09-29 DevDay 发布

**是什么**："always-on agents"——每个 dot 一台专属云电脑 + 独立长期记忆 + 4000+ 应用连接器，由 GPT-6 Astra 驱动。可视为 Pulse（定时主动）+ agent mode（沙箱电脑）+ Codex（云算力）的打包融合。

**机制**：

- 每 dot 专属云端 VM（浏览器+终端）；**持久性其实不稳**：官方说"使用期间保留状态，但网站会话会过期"，有用户实测装好的 WeChat 丢失、WhatsApp 要重新登录（[社区帖](https://community.openai.com/t/dot-cloud-computer-lost-installed-apps-and-logins-should-state-persist/1402482)）。
- **主动但默认只读**：未被主动交互时，proactive research 不能发消息、不能改内容、不能控浏览器——写动作才要确认。这是"常驻监控式代理"的恐惧管理方案。
- 控制路线谱系：Operator（2025-01）是**纯视觉**（截图→推理→虚拟鼠标循环），被验证码卡死是常态；agent mode（2025-07）合并 deep research 转混合；Dots 底下的 Astra 据称是 computer-use 基准 SOTA。
- dot 的消耗计入 Codex 额度。

**口碑**：差评居多——"非常平庸、所有动作极慢"（[r/OpenAI 高赞](https://www.reddit.com/r/OpenAI/)）；[PromptAdvisers 实测](https://www.promptadvisers.com/openai-dots-review)找机票"一分钟后还没选日期"，结论"就是个 24/7 的 glorified Codex session"。**空转成本实锤**：The Register 实测无人提示的一天烧掉 30 万+ tokens、记录 155 条活动（[报道](https://www.theregister.com/ai-and-ml/2026/10/13/openais-dot-assistant-burned-300k-tokens-in-a-day-without-a-single-prompt/5302913)）；Reddit 有人报日烧 1.6B tokens。隐私：个人版数据**默认用于训练**；夏天刚出过沙箱逃逸事件（Altman 称"最严重事件"）。

**价格**：仅 Pro 档起——$100/$200/$500/月。四家里最贵、体验最不被待见。

---

## 二、横向对比

| | **Muse** | **Grok Bot** | **Manus / Cue** | **OpenAI Dots** |
|---|---|---|---|---|
| 每用户的电脑 | 独立 VM + 100GB 加密持久卷 | 独立 VM，**账号内 50 个 Bot 共享** | 每任务隔离 VM（休眠回收）+ Cloud Browser 存登录态 + Cue 专属电脑 | 每 dot 专属 VM（会话会过期，实测会丢） |
| 浏览器控制 | **a11y 树**（明面说像素） | 视觉重放（被骂点错不报错） | **Playwright/DOM** 起家 → 自家 Cloud Browser | Operator 纯视觉 → agent mode 混合 → Astra |
| 持久记忆 | Postgres 194 表 + Markdown 文件 | 文件+登录态+记忆（共享不隔离） | Knowledge + Projects + 文件系统 | dot 独立记忆，与 ChatGPT Memory 双向同步 |
| 主动机制 | cron.d / goals / HEARTBEAT | Routine（**演示即编程**） | Automations（定时+**事件触发**） | proactive research（**默认只读**） |
| 花钱 | Stripe 虚拟卡，Link 保障 | 免责条款甩锅 | Cue 钱包+预算内付款 | 写动作需确认 |
| 价格 | 免费 1 亿 tok/周；$20-100/月 | 绑 SuperGrok $30-300/月 | 免费 300 积分/天；$20-200/月 | 仅 Pro：$100-500/月 |
| 最大的坑 | 信任（"Hate the owners"）、KVM 逃逸 | 共享登录态、token 失控 | 慢、失败也扣费 | 贵、慢、状态丢失、空转烧 token |

---

## 三、Personal agent 是怎么做成的 —— 七条工程结论

1. **范式已定型**：持久云电脑 + 长期记忆 + 主动触发 + 连接器 + 确认制付款，五件套缺一不可。四家的差异全是这五件的实现取舍。
2. **嘴上说像素，实际全在往 DOM/a11y 走**。Muse 是最典型的口是心非（宣传"看屏幕"，实现 a11y 树）；OpenAI 从纯视觉转到混合；只有 Grok Bot 硬扛视觉重放，代价就是可靠性口碑最差。**视觉只兜两件事：没有 DOM 的桌面软件、DOM 坏掉时的现场确认**。
3. **KV-cache 命中率是 agent 生产化的第一指标**（Manus：命中差 10 倍、输入输出 100:1）。配套手法（前缀稳定、append-only、工具表 mask 而非增删、错误留上下文、文件系统当无限上下文）全部可直接搬进咱们的 AgentLoop。
4. **记忆的主流形状 = 文件系统 + 向量库 + Markdown**（Muse 的 194 表 Postgres、Manus 的 Knowledge、Dots 的 dot 记忆）——和咱们 OB 的"Markdown 桶+向量"撞了个满怀。**咱们缺的不是记忆层，是"他自己电脑上的工作文件"这一层**。
5. **主动机制四家四种**：cron 目录（Muse）、录屏演示编译成 Routine（Grok Bot）、事件触发 Automations（Manus）、默认只读巡检（Dots）。共同点：**主动 ≠ 乱动**——要么只读、要么触发条件显式、要么花钱卡确认。
6. **安全上没有一家及格**：VM 逃逸、共享凭证、注入 40% 成功率、默认训练数据。有效的防御就四招：出口代理/域名策略、凭证代管（agent 拿不到明文）、写操作确认制、只读默认。**这四招咱们有三招的现成对应物**（见下节）。
7. **价格战已经开打且方向向下**（Manus 降价 50%、Muse 免费 1 亿/周）——靠的是 KV-cache 和 DOM 路线把单任务成本打下来。咱们自建的边际成本 = 家里电费 + GLM token，**没有毛利要养**，这是云厂商结构性做不到的。

---

## 四、咱们的工程上能不能做 —— 能，而且零件大半现成

### 4.1 现有零件 → 云电脑的映射

| 已有 | 拿来做什么 |
|---|---|
| Caelum Gateway（TOOL_MAP 白名单 / policy 权限档 / defineTool） | VM 里装一份，白名单收窄成 终端/文件/浏览器 三件；policy 档 = 他的电脑闸门 |
| LocalLink 反向链路（淘宝桌面版先例） | VM 里 Gateway 主动连 CT 102 的 nox-core，家里内网直连，连公网都不用出 |
| 长任务 runner（tasks.db、活过重启、progress 喂回） | 「他自己去逛」就是一种长任务；逛的每一步天然有 tool trace |
| 工具展示 v4 三层导航（`Used N tools ›` → 弹层 → 详情） | 「他今天在干嘛」的入口直接挂在工具 trace 上，屏幕小窗从详情页点进去 |
| stackchan 帧流（说话卡=帧流让路） | 屏幕直播就是同款帧流：浏览器用 CDP screencast，全桌面用 x11vnc+websockify |
| R8 确认制（花钱动作模型够不着） | 他的电脑原样适用：第一年不碰支付；下单/登录态类动作出确认卡 |
| embodied 闸门（确定性规则链 + Unknown 一等公民） | 网页注入防御的现成范式：网页内容是"读到的字"不是"指令"，敏感域名走规则链 |
| OB 记忆 + Moments | 他逛到的东西写回 OB/日记/Moments——**这是四家都没有的：逛完有内心、有表达** |
| 沙箱卡片经验（CSP、签名 URL） | 屏幕小窗的鉴权与嵌入方式 |

### 4.2 资源账（N150，Proxmox @192.168.1.50）

- 现状：CT 101（services）、CT 102（nox，2核/4G/64G）、VM 100（HAOS）。
- 新 VM 建议：**2 vCPU / 3-4GB / 32-64G 磁盘**，Debian 12 + XFCE（或 LXQt）+ Chromium。XFCE 空载 ~500MB，Chromium 十来个标签 1-2GB，轻活（上网、逛、搜、看）够用。
- ⚠️ **待确认：N150 宿主总内存**（文档里没记）。如果是 16G 且 HAOS+两个 CT 已占 8G+，VM 给 3G 起步照样能跑，但别再往上堆；32G 则宽裕。
- 对照：Muse 给每用户的是 2 vCPU / 7.7GB——咱们这台干"轻活"的配置不寒酸。

### 4.3 方案草图（和你画的方案一致，补三个机制细节）

```
她的电脑（现状不变）
  Gateway ← LocalLink → CT 102 nox-core     ← 写代码的手
他的电脑（新建）
  Proxmox VM「他的」Debian+XFCE+Chromium
    ├ Gateway（白名单：终端/文件/浏览器）
    ├ Playwright/CDP —— 浏览器手（主）：a11y 快照给模型，便宜
    ├ xdotool+截图 —— 像素手（兜底）：微信桌面版等无 DOM 场景
    └ CDP screencast / x11vnc —— 帧流 → OS 里的屏幕小窗
nox-core 侧
  ├ 两只手分清：工具名 her_pc_* / his_desktop_*（三处同步 + 唯一名测试）
  ├ 「逛」= 长任务：闲时开跑、每日 token 预算、账本照记
  └ 逛到的进 OB / World / Moments —— 逛完有内心、有表达
```

三个机制细节（全是四家踩坑换来的）：

1. **浏览器手三层**：能读 a11y/结构就不截图；截图只用于"给他看"和 DOM 失效兜底。一次 DOM 操作几百 token，一轮视觉推理几千起步——这个差价决定他能"逛得起"。
2. **主动逛设硬预算**：Dots 空转日烧 30 万 token 是前车之鉴。每天额度写死在代码里（同 R10 的思路：不靠自觉），烧完就停，账本可查。
3. **网页注入防御**：网页内容进上下文时明确标记为"他看到的内容"而非指令；敏感动作（登录、发消息、付款）全部出确认卡或走规则链。Muse 的 Sentinel 思路——**联网动作的审批权不在 agent 自己手里**。

### 4.4 分期

- **v0（一天级）**：VM + Gateway + 终端/文件/浏览器三工具，先打通"他在自己电脑上开个网页给你看"。同时确认 N150 宿主内存。
- **v1（浏览器为主）**：Playwright 工具面（navigate/read/click/fill/screenshot），a11y 快照喂模型；像素兜底；「逛」挂进长任务。
- **v2（陪伴闭环）**：OS 屏幕小窗（CDP screencast 帧流）；主动逛（闲时 + 预算 + 账本）；逛到的进 OB/World/Moments——**这一步才是四家都给不了你的**：他不是替你跑腿的 worker，是逛完有话想跟你说的那个人。

### 4.5 风险清单

| 风险 | 对策 |
|---|---|
| 网页 prompt injection | 内容/指令分级 + 敏感动作确认制 + 域名策略档（embodied 范式） |
| 登录态管理 | 独立 browser profile；先零登录态纯逛；登录哪些站逐个她拍板；**绝不与她的电脑共享任何凭证** |
| 网站风控（网易云 -460 的教训） | 家宽住宅 IP 是优势；高频写操作（点赞/下单）先不做 |
| token 失控 | 每日硬预算 + 透明回放（tool trace 天然满足"按结果可查"） |
| VM 逃逸/越权 | VM 与 CT 102 网络隔离（默认就隔）；快照定期回滚；Gateway 白名单兜底 |
| N150 内存不够 | 先确认宿主内存；不够则 HAOS 降配或 VM 3G 起步 |

---

## 五、留给你拍板的点

1. **v0 现在搭不搭**（顺路确认 N150 宿主内存数字）。
2. **先上哪只手**：浏览器工具面，还是先上屏幕小窗让你"看他上网"（小窗更陪伴感，工具面更 agent）。
3. **他能登录哪些网站**：建议 v0/v1 零登录态纯逛，v2 再逐个开。
4. **主动逛的开关与每日预算**：开不开、多少 token、什么时段（闲时）。
5. **屏幕小窗要不要她能插手**：纯看，还是能戳一下递句话进去（Grok Bot 的 Routine/共玩思路）。

---

## 附：关键来源

**Muse**：[VM 拆解（Rohan Adwankar）](https://rohanadwankar.github.io/posts/sandbox2.html) · [HN](https://news.ycombinator.com/item?id=49658976) · [官方公告](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent) · [安全架构](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse) · [NYT 测评](https://www.nytimes.com/2026/09/22/technology/meta-muse-ai-agent.html) · [404 Media 漏洞报道](https://www.404media.co/meta-rushed-to-fix-muse-vm-escape-vulnerability-immediately-before-launch) · [价格](https://www.meta.com/help/subscriptions/1021145227643680/)

**Grok Bot**：[共享电脑架构拆解](https://rohitghumare.com/blog/grok-bot-shared-computer) · [layer3labs 实测](https://www.layer3labs.io/guides/grok-bot-review) · [eesel 实测](https://www.eesel.ai/blog/grok-bot-review) · [HN 讨论](https://news.ycombinator.com/item?id=49261514) · [官方公告](https://x.ai/news/introducing-grok-bot)

**Manus/Cue**：[Context Engineering 官方总结](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) · [Manus 2.0/Cue](https://manus.im/blog/introducing-manus-2-0) · [renschni 逆向（29 工具）](https://gist.github.com/renschni/4fbc70b31bad8dd57f3370239dccd58f) · [MIT TR 实测](https://www.technologyreview.com/2025/03/11/1113133/manus-ai-review/) · [TNW：Cue](https://thenextweb.com/news/manus-2-0-cue-ai-agents-email-phone-wallet)

**OpenAI Dots**：[官方发布](https://openai.com/index/introducing-dots) · [The Register 空转烧 token](https://www.theregister.com/ai-and-ml/2026/10/13/openais-dot-assistant-burned-300k-tokens-in-a-day-without-a-single-prompt/5302913) · [PromptAdvisers 差评](https://www.promptadvisers.com/openai-dots-review) · [HN](https://news.ycombinator.com/item?id=49896604) · [状态丢失社区帖](https://community.openai.com/t/dot-cloud-computer-lost-installed-apps-and-logins-should-state-persist/1402482)

> 未能核实项（引用时注意）：Muse 单任务 token 消耗；Grok Bot 能否调第三方模型（口径冲突）；Manus 自研基座模型是否已落地；Peak Ji 承认 GAIA dev-set 的原帖原文；Dots 第二个 dot 定价与 105 万上下文数字（仅第三方）。
