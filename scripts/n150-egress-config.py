"""生成 N150 出口代理（xray 客户端）的配置（2026-10-10）。

为什么要有它：N150 在国内，OpenRouter 对国内 IP 拒 Claude / GPT（403 not available in your region）。
这里在 N150 本机开一个代理口，连她的 Reality 节点（先东京，11 月迁 RackNerd 时改 SERVER 一行）。

🔴 **白名单出口**：只有下面几个海外域名走节点，其他一律直连 —— 哪个程序配错了、把国内请求也送进来，
   也不会绕出去（国内模型 / 豆包 / 阿里绕一圈只会更慢，还多一个会挂的依赖）。
   节点连接参数在 /root/.reality-client.json（600），从节点的 x-ui 库取的，**不进仓库**。

用法（N150 上）：python3 n150-egress-config.py > /etc/xray-egress/config.json
"""

import json
from pathlib import Path

#: 节点地址直接写 IP：panel.noxtang.com 在家宽上同样会被 DNS 劫持
SERVER = "43.153.154.237"

#: 🔴 10-10 晚改：手写白名单永远补不全（第一版漏了 Google News —— 话题池从搬家起就抓不到），
#: 改成节点自带的地理规则库（/usr/local/share/xray/geosite.dat、geoip.dat，从东京节点拷来）：
#:   国内网站 / 国内 IP / 局域网 → 直连；**所有非中国网站 → 节点**；拿不准的 → 直连。
#: 下面这几个单独再列一遍：它们是功能依赖（区域限制 / 被墙），万一规则库分类变了也不能掉
MUST_PROXY = [
    "domain:openrouter.ai",       # 按地区限模型：国内 IP 调 Claude / GPT 403
    "domain:anthropic.com",
    "domain:openai.com",
    "domain:elevenlabs.io",       # 直连能通，但经东京更快（10-10 实测 TTS 首字节 621→434ms）
    "domain:googleapis.com",
    "domain:news.google.com",     # 话题池
    "domain:ycombinator.com",
    "domain:wikipedia.org",
    "domain:github.com",
    "domain:githubusercontent.com",
]
#: 国内的、走节点只会更慢的：明确直连（规则库里大多已经是 cn，这里兜底）
MUST_DIRECT = [
    "geosite:cn",
    "domain:noxtang.com",
    "domain:deepseek.com", "domain:bigmodel.cn", "domain:aliyuncs.com", "domain:bytedance.com",
    "domain:amap.com", "domain:didichuxing.com", "domain:mcd.cn", "domain:lkcoffee.com",
    "domain:kuaidi100.com", "domain:163.com", "domain:126.net", "domain:bilibili.com",
]

p = json.loads(Path("/root/.reality-client.json").read_text())

config = {
    "log": {"loglevel": "warning"},
    "inbounds": [
        {"tag": "socks", "listen": "127.0.0.1", "port": 10808, "protocol": "socks",
         "settings": {"udp": False}},
        {"tag": "http", "listen": "127.0.0.1", "port": 10809, "protocol": "http"},
    ],
    "outbounds": [
        {"tag": "direct", "protocol": "freedom"},
        {
            "tag": "node",
            "protocol": "vless",
            "settings": {"vnext": [{
                "address": SERVER, "port": p["port"],
                "users": [{"id": p["id"], "encryption": "none", "flow": p["flow"]}],
            }]},
            "streamSettings": {
                "network": "tcp",
                "security": "reality",
                "realitySettings": {
                    "serverName": p["sni"], "fingerprint": p["fp"], "publicKey": p["pbk"],
                    "shortId": p["sid"], "spiderX": p["spx"],
                },
            },
        },
        {"tag": "block", "protocol": "blackhole"},
    ],
    "routing": {
        # IPIfNonMatch：域名规则都没命中时，解析出 IP 再按 geoip 判（国内 IP 直连）
        "domainStrategy": "IPIfNonMatch",
        "rules": [
            {"type": "field", "domain": MUST_PROXY, "outboundTag": "node"},
            {"type": "field", "domain": MUST_DIRECT, "outboundTag": "direct"},
            {"type": "field", "ip": ["geoip:private", "geoip:cn"], "outboundTag": "direct"},
            {"type": "field", "domain": ["geosite:geolocation-!cn"], "outboundTag": "node"},
            # 都没命中的一律直连 —— 第一个 outbound 就是 direct，这条写出来是为了读的人放心
            {"type": "field", "network": "tcp,udp", "outboundTag": "direct"},
        ],
    },
}
print(json.dumps(config, indent=2, ensure_ascii=False))
