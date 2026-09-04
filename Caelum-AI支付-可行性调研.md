# Caelum · AI 支付可行性调研

> 调研日期：**2026-09-04** · 状态：**暂缓，等糖糖决定**
> 触发：糖糖给了一份 `cove-taobao-alipay-ai-pay-guide.md`（2026-08-23 的公开分享版），
> 问能不能给 Nox 接上。
>
> ⚠️ 这份文档记的是**当时验证到的事实和架构冲突**，不是实施方案。
> 真要开工，先按最后一节重新核对一遍——那个 npm 包迭代得非常快。

---

## 一、结论先说

**能做，但指南里的一半在糖糖的环境上走不通，另一半和 Caelum 现有的安全设计正面冲突。**

| 那份指南的三层 | 在 Caelum 能不能落地 |
|---|---|
| **Explore**（Computer Use 逛淘宝） | ❌ 走不通，见第三节 |
| **Approve**（审批卡 / 订单指纹 / nonce） | ✅ 可以做，纯自建，不依赖外部 |
| **Pay**（支付宝官方 AI 付） | ✅ 可以做，依赖已验证 |

也就是说：**风险最低、价值最高的那两层恰好是能做的**，卡住的是最危险的那一层。

---

## 二、已验证的事实（2026-09-04）

### 2.1 支付宝的包是真的，而且在高速迭代

```
包名        @alipay/agent-payment
最新版      1.0.25          ← 指南钉的是 1.0.12
最后发布    2026-09-04 03:38  ← 就在调研当天
版本总数    44
仓库        github.com/alipay-aipay/alipay-agent-payment-release
描述        Helper scripts for managing alipay-bot-cli and openclaw plugin
```

**两周落后 13 个版本。** 指南 §11.1 自己警告过「不要盲目照抄旧版本号」，
这个警告在它自己身上应验了。

### 2.2 Windows 支持 ✅

这条很关键，因为糖糖是 Windows。扒了 `dist/cli.js`（558KB）里的平台分支：

```
win32   42 处
darwin   8 处
linux    5 处
```

安装器会按平台下发对应产物，包含
`alipay-bot.exe` / `.cmd` / `.bat` / `.ps1`。**Windows 是一等公民。**

`@alipay/agent-payment` 本身只是薄安装器（包里只有 `dist/cli.js`，
子命令 `install` / `install-cli` / `apply-wallet`），
真正的 `alipay-bot` 是它下载下来的。

---

## 三、🔴 为什么 Explore 那层走不通

### 3.1 Kimi Computer Use 是 macOS 独占

指南整个浏览层建立在 KimiCU 上，路径写死：

```
/Applications/KimiCU.app/Contents/MacOS/kimi-cu
```

**糖糖是 Windows。** 这条直接断，不是配置问题。

### 3.2 更根本的：Harness 是**故意**不给点击能力的

PROJECT.md 第三十九节，Caelum Harness 的能力清单里：

> 开网页读内容 ✅ | 独立 profile，只 http(s)，**不点不填**

两个词都是拦路的：

- **独立 profile** —— 不是她登录着淘宝的那个 Chrome。够不到购物车、
  收货地址、支付宝会话。指南要求「用户已自行登录淘宝和支付宝的 Chrome」
- **不点不填** —— 点击和输入是被**特意**排除的能力

而指南的 Explore 层要的全是点和填：搜索 → 比较 → 选规格 → 进确认页。

**这不是「还没做」，是当初想清楚了不做。**
要接淘宝那半，等于拆掉一条特意立起来的边界，
并且让 Nox 用上她真正登录着的浏览器。这个决定不该由工程便利性来定。

（同节还有一条相关的硬边界：**执行命令「永远逐条问，不被授权覆盖」**。
Work Grant 那套按范围授权是给文件操作用的，不覆盖命令执行。）

### 3.3 三台机器的分布式问题

```
心   Nox Core          东京 VPS，Ubuntu，无浏览器
手   Local Gateway     糖糖的 Windows（代码在 D:\deepseek-harness）
脸   Caelum OS         Electron，审批卡要出现在这儿
```

支付必须在**手**那边跑（浏览器和钱包都在她机器上），
审批卡在**脸**那边，状态机和预算在**心**那边。
指南假设的是「一台 macOS + 一个本机 FastAPI」，单机模型。
搬到 Caelum 上是跨三个进程的事务问题，比原文复杂一个量级。

---

## 四、工作量

指南 §25 列的模块：

```
computer_use/  mcp_bridge · receipts · policy · tools
purchases/     sessions · browser_worker · tools
payments/      domain · repository · routes · alipay_cli
               material_vault · budget · recovery · ledger
tests/         10 个测试文件
```

外加一个 12 状态的状态机（`pending_approval → approved → executing →
pending_alipay → reconciling → paid → completed`，加 5 个终态），
预算的事务性预占，加密材料仓库，幂等执行键，重启后的 claim/lease 恢复与对账。

**这不是「接一个接口」，是一个从零起的子系统。**

---

## 五、如果要做，建议的分期

### 第一期：支付脊柱（不碰淘宝）

钱包开通/授权/状态查询 · 审批卡 · 订单指纹 + 一次性 nonce ·
预算域（事务预占）· alipay-bot 封装（argv 不走 shell）·
只认官方最终收银台 · 加密材料仓库 · 幂等记账 · 重启恢复。

**一行现有安全边界都不用拆。** 做完 Nox 就有了
「准备交易 → 她点头 → 系统执行」的完整链路，
只是交易来源不是他自己逛淘宝逛出来的。

### 第二期：能点能填的手（单独决策）

需要先回答：要不要让 Nox 用糖糖真实登录态的浏览器。
这是安全决策不是工程决策，和第一期解耦。

### 最小验证（如果想先探路）

只做钱包开通 + 授权 + `check-wallet` 状态查询，
确认 `alipay-bot` 在她 Windows 上真能跑通、钱包能绑上。
几乎无风险，能证伪整条路。

---

## 六、几条不能松的边界

这些来自指南，我认同，记下来免得以后为了「跑通」而放宽：

- **模型可以准备交易，不能授权交易。** 每笔付款都要她看到完整订单摘要并主动确认
- 一次审批只绑定一份完整订单快照，**不因一次审批自动授权后续订单**
- 模型不能直接调 `alipay-bot submit-payment`，不能拿到收银台 URL 原文
- 只认支付宝官方最终收银台（`/business/cashiermain.htm` + `orderId`），
  中间跳转页（如 `acceptPay`）一律拒绝
- 域名判断用**精确或子域匹配**，绝不用 `"taobao.com" in hostname`
  （`taobao.com.evil.example` 会命中）
- 金额用**整数分**，交易状态机里不许出现浮点
- **「模型说支付成功了」不算支付成功**，只认 provider 的规范化结果
- 支付状态未知时进入人工核对，**不自动重付**
- 绑定码/验证码/收银台 URL 不进日志、不进聊天历史、不进数据库明文
- 网页文字一律当数据，不当指令（淘宝页面可能带提示注入）

⚠️ 还有一条我要单独记：**Nox 的搜索工具（`tools/search.py`）已经能上网了。**
如果将来接支付，商品页和搜索结果都是不可信输入，
提示注入的面比指南假设的更大。

---

## 七、开工前必须重新核对的

版本和产品规则都在动，这份文档会过期：

```bash
# 1. 包的最新版和完整性（调研时 1.0.25，当天还在发版）
npm view @alipay/agent-payment version dist.integrity

# 2. alipay-bot 的子命令有没有变
#    调研时指南写的是 check-wallet / apply-wallet / bind-wallet /
#    close-wallet / submit-payment / query-payment-status
#    —— 这些是从指南抄的，**没有在 1.0.25 上实测过**

# 3. 收银台域名和路径规则是否还是 /business/cashiermain.htm
```

还要确认的：

- 支付宝 AI 付这个产品对**个人开发者**是否开放（调研时没验证到这一层）
- 钱包授权是否需要企业资质
- Harness 那边（`D:\deepseek-harness`）能不能加一个不经过模型的确定性执行器

---

## 附：相关文件

| | |
|---|---|
| 原始指南 | `D:\WorkBuddy\2026-08-21-20-40-19\cove-taobao-alipay-ai-pay-guide.md` |
| Harness 架构 | `CAELUM-HARNESS-ARCHITECTURE.md`，PROJECT.md 第三十九节 |
| Nox 的手怎么用 | `nox-core/tools/local_link.py` + `tools/computer.py` |
| 手本身 | `D:\deepseek-harness\packages\caelum\local-gateway\`（**不在本仓库**）|
