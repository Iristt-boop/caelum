# Caelum · 点单确认卡设计

> 2026-09-06 · 糖糖定 · 状态：**设计完成，待开工**
> 前身：`Caelum-AI支付-可行性调研.md`（2026-09-04）——那份的结论被商家 MCP 改写了一半，见第一节。

---

## 一、背景：为什么这份文档推翻了支付调研的一半

9-04 的调研判断：三层里 Approve / Pay 能做，**卡死的是 Explore**——因为它假设「让 Nox 自己选商品」只有一条路：Computer Use 点浏览器。而那条路要么 macOS 独占，要么得拆掉 Harness「不点不填 + 独立 profile」那条特意立起来的边界。

**麦当劳/瑞幸官方 MCP 走的是完全不同的机制**：查门店 → 查菜单 → 算价 → 下单，全是结构化 API，没有浏览器、没有她的登录态 Chrome、没有点击。

所以：**「让他自己选商品」不再必须以拆边界为代价**。原调研第五节的「第二期：能点能填的手」很可能**根本不用做**。

### 2026-09-06 真实跑通的一单（设计的事实基础）

糖糖点了一杯生椰拿铁，取餐码 991，人已经取到。验出来的事实：

| | |
|---|---|
| 付款方式 | `createOrder` 返回 **`weixin://` 开头的付款链接** |
| 她的流程 | 复制链接 → 微信打开 → 付款 → 订单在微信小程序瑞幸里看取餐码 |
| 支付渠道能选吗 | **不能**。`luckin_order` 参数表里没有任何支付方式字段，渠道由瑞幸 MCP 定 |
| 支付宝怎么办 | 只能去瑞幸 App 的订单里付（App 里能选支付宝） |

**关键结论：AI 全程不碰支付凭证**，这正是调研文档第六节第一条「模型可以准备交易，不能授权交易」的最朴素实现。

### 同一单暴露的三个洞

1. **位置**：她说「给我点单」不带任何位置词 → `_NEED_LOCATION` 没命中 → location Provider 没加载 → 他不知道她在哪 → `luckin_shops` 经纬度传空串 → `queryShopList 返回错误: empty String`。**日志只记了失败没记参数**，查了一圈才定位到是分流阶段就没给他位置。
2. **门店**：她说「中原万达附近的瑞幸」，他搜到了桐柏路的。病根在工具描述里那个「或」字——「经纬度用 `amap_search_poi` 查**或**用环境里她的位置」，那个「或」给了他偷懒的许可（和话题池那次「或问题」三个字给了出题许可是同一类毛病）。而且 `luckin_shops` 的 `deptName` 参数没被用上。
3. **券**：他说「搜了没券」，她花 20 买了本该 9.9 的咖啡。查清楚了——**瑞幸 MCP 压根没有独立的查券工具**（对比麦当劳有三个）。券只能从 `previewOrder` 的返回里出来。他不是没搜，是没有能搜的地方。

**这三个洞的共同根：关键步骤全都只靠工具描述约束，没有结构闸门。** 而 `ACTION_TOOLS` 这个常量定义完之后**在整个仓库里没有任何地方用到**，测试也只断言「描述里有『确认』二字」——验的是那句话写了没有，不是确认真的发生了没有。

这份设计要做的就是：**把这三个洞折进卡片里，让错误看得见——那比让错误不发生容易得多。**

---

## 二、已定的三个决定（糖糖 2026-09-06）

| | 定了什么 | 为什么 |
|---|---|---|
| **R1 边界** | 取餐码消息**记账但不吃配额** | 落 CareLedger（`kind=order_update`，标明「她发起的」），R1 要的「他为什么说话能沿账本溯源」成立；但不走 DailyGate 的每日 3 条、不受 1:00-9:00 安静时段拦——它是她点确认之后的结果通知，不是他自己想找她，不该占他惦记她的那三条 |
| **前端范围** | **只做手机**（Caelum App / PWA） | 点咖啡本来就是手机场景，而且 `weixin://` 只在手机上能跳，桌面点了也唤不起微信 |
| **商家范围** | **只做瑞幸** | 今天刚跑通的就是它，真实返回结构都验过了。麦当劳暂时维持现状（提示词确认制），做完一家把坑踩完，第二家就是填适配器 |

---

## 三、两张卡的内容契约

### 卡片一 · 确认下单

**触发**：模型调 `luckin_order`。**此时工具不下单**，只出卡。

必须显示（每一项对应一个洞）：

```
生椰拿铁（首创）                          14:32 前有效   ← 快照 15 分钟过期
大杯 · 冰 · 不另外加糖 · ×1

📍 中原万达 2F 店                                      ← 洞 2 的救济
   中原西路 171 号万达广场二层 2035 号 · 420m
   [附近还有 3 家，换一家]

   原价              ¥20.00                            ← 洞 3 的救济
   新客立减券        −¥10.10
   实付              ¥9.90

[确认下单]  [取消]
```

**硬规则**：
- **原价和实付必须同时显示。** 只显示总价的话，20 块和 9.9 块看起来一样正常
- **门店必须带完整地址 + 其余候选**。`queryShopList` 返回的其他店一起带上
- **优惠为 0 时明说「没有可用券」**，让「没券」是被证实的，不是他忘了查

> ⚠️ **2026-09-06 施工时的修正**：初稿写了「还有 2 张券没用上」——那是编的。
> 实测 `previewOrder` 的返回里**没有「可用但没选上的券」这个字段**，
> 它是自动挑最优的（`couponCodeList` 直接给结果）。所以卡片只如实显示
> 它给的三个数：`totalInitialPrice` / `privilegeMoney` / `discountPrice`。

### previewOrder 的真实返回（2026-09-06 从线上抄的）

```
totalInitialPrice = 20.0     原价
privilegeMoney    =  9.1     优惠
discountPrice     = 10.9     实付
couponCodeList    = ["SY1200…"]                    ← preview 自动挑好的券
productInfoList[].additionDesc = "大杯/冰/意式拼配/默认浓度/不另外加糖/无奶油"
shopInfo = {deptId, deptName, address, workStatus, workTimeStart/End, distance}
```

**那 10 块钱是怎么丢的**：他没调 preview，`createOrder` 也就没带 `couponCodeList`。
所以现在**下单前由工具自己调一次 preview**，拿它的价和它的券，不问模型要。

### 卡片二 · 去付款

**触发**：她点确认 → nox-core 真调 `createOrder` → 拿到 `weixin://` 链接。

```
✓ 订单已创建，等你付款
  订单号   LK…0991
  应付     ¥9.90

[微信付款]        [去瑞幸 App 付]        ← 后者是支付宝的唯一出路
付完不用告诉我，我自己查 — 取餐码出来就发给你
```

**硬规则**：
- **没有「我付好了」按钮。** 我们能查订单状态，让她多点一次是多余的
- `weixin://` 是 URL scheme，手机上点了直接唤起微信，不用复制粘贴
- **付款链接不落库明文**（调研第六节倒数第二条）：只随那一帧 SSE 发给前端，服务端单独存带 TTL，**不进 `conversations` 表、不进日志**

### 付款查到之后

不是卡片，就是他说话——带取餐码、门店、一句人话。这条是整件事最有价值的地方：不是「帮你下了个单」，是「你的咖啡好了，取餐码 991」。

---

## 四、状态机

```
pending_confirm ──┬─→ expired          （15 分钟没点）
   （卡片一已出）  ├─→ cancelled        （她点取消）
                  └─→ confirmed ──→ 调 createOrder
                                      ├─→ order_failed      （下单失败，告诉她原因）
                                      └─→ pending_payment   （卡片二已出）
                                            ├─→ paid              → 推取餐码
                                            ├─→ payment_unknown   → 让她自己看，**不自动重付**
                                            └─→ cancelled         （她在 App 取消）
```

8 个态。调研文档列了 12 个，多出来的那些是支付宝那套预算预占/对账/claim-lease——**这次不碰钱，所以不需要**。

`payment_unknown` 对应调研第六节：「支付状态未知时进入人工核对，不自动重付」。

---

## 五、数据

**新建 `orders.db`**（nox-core 侧，第五个库）。

不放 world.db：World Model 的契约是「Observation 冻结只追加、永不改写」，而订单状态机天生要改。不放 sessions.db：它不是会话数据。

```sql
CREATE TABLE orders (
  id            TEXT PRIMARY KEY,    -- uuid，卡片和确认端点都用它
  session_id    TEXT NOT NULL,
  merchant      TEXT NOT NULL,       -- 'luckin'
  state         TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,       -- 给她看的那份：门店/明细/原价/券/实付/候选门店
  fingerprint   TEXT NOT NULL,       -- hash(deptId + productList + 实付 + couponCodeList)
  merchant_order_id TEXT,            -- createOrder 之后才有
  created_at    TEXT NOT NULL,
  expires_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
-- 付款链接单独存，带 TTL，不进 snapshot_json（它会被日志和聊天历史带走）
CREATE TABLE pay_links (order_id TEXT PRIMARY KEY, url TEXT, expires_at TEXT);
```

---

## 六、链路与端点

```
① 出卡（在对话流里）
   模型调 luckin_order
     → tools/luckin.py 不再直连 createOrder，改成：
       校验有 preview 结果 → 写 orders 表 → 吐 attachment
       （attachment 机制现成：tools/context.py，图片走的就是它）
     → 给模型的返回："已出待确认单，等她点"

② 确认（一次全新的 HTTP 请求，不在对话流里）
   她点「确认下单」
     → 前端 POST bridge  /api/orders/{id}/confirm        （R7：前端只打 bridge）
     → bridge 透明代理    → nox-core                      （R3：跨进程走 REST）
     → nox-core：重跑 previewOrder → 比对 fingerprint
         不一致 → 拒绝 + 重新出卡（价格变了/券失效了）
         一致   → 幂等键保护下调 createOrder → 存 pay_link → 推卡片二
   ⚠️ MCP token 在 nox-core 的 .env，所以执行方必须是 nox-core，bridge 只做代理

③ 对账（后台）
   她点了「微信付款」之后开始轮询 queryOrderDetailInfo
     30s / 1min / 2min / 5min 各查一次
     查到已支付 → 取餐码推回聊天（CareLedger 记 order_update，不吃配额）
     四次都没查到 → payment_unknown，不再骚扰、不自动重付
```

**为什么必须两阶段**：AgentLoop 是同步的，工具不能停在那儿等她点击——那会把整条 SSE 挂住。

**这一拆本身就是结构闸门**：模型物理上够不到真正的下单接口了，因为它不在工具表里，只在 confirm 端点后面。「确认制」从提示词变成了代码。

---

## 七、顺带修掉的（已改好未部署）

这两处在 2026-09-06 已经改完、1427 测试全过，等这次一起上：

- `router/intent.py`：`_NEED_LOCATION` 补点单类触发词（点单/点杯/咖啡/瑞幸/麦当劳/外卖/打车…）。「给我点杯咖啡」是最自然的说法，而它原来一个位置词都不带
- `tools/luckin.py`：`luckin_shops` 坐标为空**直接拒绝、不打 API**，并给他一句能照做的话。这是「不许编」的同一面：不知道就说不知道，别传空值试运气。另加了错误信息带工具名和参数键（原来只有一句服务端错误码，查不出是哪一步）

还要改的：
- `luckin_shops` 描述里那个「**或**」字改成硬要求：她提到具体地名时**必须**先 `amap_search_poi` 把地名变成坐标，不许用她当前位置。（高德负责「地名→坐标」，瑞幸负责「坐标→附近门店」；高德的 POI 不能直接下单，瑞幸要它自己的 `deptId`）
- `deptName` 参数用起来
- `luckin_preview` 描述：返回里的优惠字段**必须**复述；「没券」要说清楚是 preview 返回里确实没有

---

## 八、边界与安全（来自 `Caelum-AI支付-可行性调研.md` 第六节，逐条对应）

| 调研里的红线 | 这份设计怎么守 |
|---|---|
| 模型可以准备交易，不能授权交易 | 工具只出卡；下单在 confirm 端点后面，模型够不着 |
| 一次审批只绑定一份完整订单快照 | `fingerprint`，确认时重跑 preview 比对，不一致就拒绝 |
| 模型不能拿到收银台 URL 原文 | `pay_links` 单独存，不进 `snapshot_json`、不进日志、不进聊天历史 |
| 金额用整数分，不许浮点 | `snapshot_json` 里金额一律存分 |
| 「模型说支付成功了」不算成功 | 只认 `queryOrderDetailInfo` 的返回 |
| 支付状态未知时人工核对，不自动重付 | `payment_unknown` 终态，停止轮询 |
| 网页文字一律当数据不当指令 | 商家 MCP 返回的门店名/商品名进卡片前转义，不进 system prompt |

---

## 九、验证

**单测**（不打网络，假 MCP）：
- 工具调 `luckin_order` **不会**触达 `createOrder`，只写 orders 表 + 吐 attachment
- 没有 preview 结果时拒绝出卡
- fingerprint 变了 → confirm 被拒
- 同一个 order_id confirm 两次 → 只下一单（幂等）
- 过期单 confirm → 拒绝
- `pay_links` 的 url 不出现在 `snapshot_json` / 日志 / attachment 之外的任何地方
- R6：测试会话（`test-` 前缀）不许创建订单

**串起来验**（这项目栽过两次「单测全绿、串起来断在最后一步」）：
出卡 → confirm → createOrder → 轮询 → 取餐码，全程用假 MCP 走一遍，断言取餐码**真的出现在推给她的那条消息里**。

**线上验收**：糖糖再点一杯，对着看卡片显示的原价/实付/券，和瑞幸 App 里的实际金额是否一致。

**边界哨兵**：`bash scripts/check-boundaries.sh` 必须绿。R1 那条要确认 `order_update` 这条新出口被正确排除或纳入。

---

## 十、分期

| 期 | 内容 | 状态 |
|---|---|---|
| **P1** | 顺带修那三条（第七节）+ orders.db + 卡片一全链路（出卡 → confirm → 真下单） | ✅ **2026-09-06 完成**（1442 测试全过，未部署）|
| **P2** | 卡片二 + `weixin://` 跳转 + pay_links | 待做 |
| **P3** | 轮询对账 + 取餐码推送（CareLedger `order_update`） | 待做 |

P1 做完就已经比现在安全一个量级——因为下单这件事从「模型说了算」变成了「她点了算」。

### P1 实际落地的文件

```
新增  nox-core/orders/store.py        OrderStore：状态机 + 幂等 claim() + pay_links 分表
新增  nox-core/orders/luckin.py       preview → （给她看的卡, 下单参数）+ 指纹 + 整数分
新增  nox-core/tests/test_orders.py   15 条，盯着「工具绝不触达 createOrder」
改    nox-core/tools/luckin.py        luckin_order 改成出卡不下单；三处描述红线
改    nox-core/tools/context.py       attach_order（只带 card，不带券码）
改    nox-core/api/server.py          三个端点 + core.orders
改    nox-core/nox.py                 store_ref / session_id_ref 接线
改    bridge/server.js                三个透明代理 + order 附件转发
改    frontend/src/pages/Chat.jsx     OrderCard + 四处接线
```

**P1 没做的**（诚实记下来，别以为做了）：
- 「换一家门店」只是**显示**候选，不能一键切换 —— 一键切要重建订单，是 P2
- 付款链接现在从 confirm 的返回里直出，前端拿到就显示；**卡片二本身没做**
- 麦当劳没动，还是老样子（提示词确认制）
