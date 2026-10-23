"""登录：她填表、我不经手（Nox 的电脑 v2，2026-10-23；设计稿 5.2）。

两条红线：
  ① 密码永远不越过 Nox 这条线 —— 她的手机用这张卡的一次性公钥加密，bridge / Core 只转发密文，
     明文只在她的设备和这台小电脑的内存里出现过；不落盘、不进日志
  ② 密码只交给她认可过的网站 —— 卡片上的域名取自浏览器真实地址（不是他说的）；放行比完整 origin；
     发卡时和填值时必须是同一个页面、同一个 origin、同一批元素，变了就不填

加密：ECDH P-256（她那边每次现生成一把临时钥匙）→ HKDF-SHA256（salt = login_id）→ AES-256-GCM。
浏览器 WebCrypto 原生支持这一套，不用装任何库。密文里同时封进 login_id，解开后再比一次。
"""

from __future__ import annotations

import base64
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

#: 发卡后多久没填就作废（私钥跟着销毁）
CARD_TTL_S = 180
#: 凭证保护的兜底上限：到了作废登录、清值（不是「保护悄悄消失、页面还在」）
PROTECT_MAX_S = 1800
HKDF_INFO = b"caelum-login-v1"

#: 只给 selftest 用：本机测试站是 http，正式永远不给 http 填
ALLOW_HTTP_HOSTS: set[str] = set()

#: 只有这几种多段后缀要多看一层（够用就好；不认识的按最后两段算）
_MULTI = {"com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn", "co.uk", "org.uk", "ac.uk",
          "com.hk", "com.tw", "co.jp", "ne.jp", "or.jp", "com.au", "net.au", "co.kr", "com.sg", "com.my"}


def site_of(host: str) -> str:
    """主域名（给她看的那个大字）：login.taobao.com → taobao.com；taobao.com.evil.com → evil.com"""
    host = (host or "").lower().rstrip(".")
    parts = host.split(".")
    if len(parts) <= 2 or host.replace(".", "").isdigit():
        return host
    return ".".join(parts[-3:]) if ".".join(parts[-2:]) in _MULTI else ".".join(parts[-2:])


def origin_of(url: str) -> str:
    u = urlsplit(url or "")
    port = u.port or (443 if u.scheme == "https" else 80)
    return f"{u.scheme}://{(u.hostname or '').lower()}:{port}"


def check_origin(url: str) -> str | None:
    """能不能在这一页登录。不能 → 回原因"""
    u = urlsplit(url or "")
    host = (u.hostname or "").lower()
    if not host:
        return "现在没有打开的页面。"
    if "xn--" in host:
        return f"这个网址是编码过的国际化域名（{host}），容易被拿来冒充别的网站，不在这里登录。"
    if u.scheme != "https" and host not in ALLOW_HTTP_HOSTS:
        return "这个页面不是 https，密码会明文在网上跑，不在这里登录。"
    return None


#: 在页面里认登录框：密码框 + 它前面那个账号框 + 验证码框；没有密码框就认「手机号 + 验证码」。
#: 认到的元素打上 data-nox-cred（之后填值、核对、截图涂掉都认这个标记），回给 Python 的只有种类和标签
FIND_FIELDS_JS = r"""
() => {
  for (const el of document.querySelectorAll('[data-nox-cred]')) el.removeAttribute('data-nox-cred');
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && !el.disabled && !el.readOnly;
  };
  const inputs = [...document.querySelectorAll('input, textarea, [contenteditable="true"]')].filter(visible);
  const typeOf = (el) => (el.getAttribute('type') || 'text').toLowerCase();
  const hint = (el) => [el.getAttribute('autocomplete'), el.name, el.id, el.getAttribute('placeholder'),
                        el.getAttribute('aria-label')].join(' ').toLowerCase();
  const isCode = (el) => /one-time-code|otp|captcha|verif|sms|code|验证码|校验码|动态码/.test(hint(el)) && typeOf(el) !== 'password';
  const isUser = (el) => ['text', 'email', 'tel', 'number', ''].includes(typeOf(el)) && !isCode(el);
  const label = (el) => {
    const byFor = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    return (el.getAttribute('aria-label') || el.getAttribute('placeholder') || (byFor && byFor.innerText) ||
            el.name || '').replace(/\s+/g, ' ').trim().slice(0, 30);
  };
  const out = [];
  const mark = (el, kind) => {
    el.setAttribute('data-nox-cred', kind);
    out.push({key: kind, kind, label: label(el)});
  };
  const pass = inputs.find((el) => typeOf(el) === 'password');
  const scope = (pass && pass.form) || document;
  const inScope = inputs.filter((el) => scope === document || el.form === scope);
  if (pass) {
    const before = inScope.filter((el) => el !== pass && isUser(el) &&
      (el.compareDocumentPosition(pass) & Node.DOCUMENT_POSITION_FOLLOWING));
    const user = before[before.length - 1];
    if (user) mark(user, 'user');
    mark(pass, 'pass');
  } else {
    const user = inScope.find((el) => typeOf(el) === 'tel' || /phone|mobile|手机/.test(hint(el))) ||
                 inScope.find((el) => typeOf(el) === 'email' || /user|account|login|账号|用户名|邮箱/.test(hint(el)));
    if (user) mark(user, 'user');
  }
  const code = inScope.find((el) => isCode(el));
  if (code) mark(code, 'code');
  return out;
}
"""

#: 页面嵌着登录框的 iframe 吗（这一版只在主页面上登录）
HAS_FRAME_PASSWORD_JS = r"""
() => [...document.querySelectorAll('iframe')].some((f) => {
  try { return !!f.contentDocument && !!f.contentDocument.querySelector('input[type=password]'); }
  catch (e) { return true; }   // 跨域 iframe 看不进去 —— 当作可能有
})
"""

#: 凭证保护中：截图前把凭证框涂成实心块（不指望网页显示圆点）
COVER_CSS = ("[data-nox-cred]{color:#3a3a3a!important;-webkit-text-fill-color:#3a3a3a!important;"
             "background:#3a3a3a!important;caret-color:transparent!important}")


@dataclass
class Card:
    """一张登录卡：login_id + origin + 页面 + 那几个元素 + 公钥 + 过期时间是一个整体，私钥跟着卡走"""

    login_id: str
    origin: str
    url: str
    page: Any
    fields: list[dict]
    private_key: Any
    public_spki: str
    expires: float

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) > self.expires


@dataclass
class Protection:
    """凭证保护是一个状态（设计稿 5.2）：这次的值只用来把它交出去的文字里的原文抹掉；结束时丢掉"""

    origin: str
    values: list[str] = field(default_factory=list)
    until: float = 0.0

    def active(self, now: float | None = None) -> bool:
        return bool(self.values) and (now or time.time()) <= self.until

    def scrub(self, text: str) -> str:
        if not self.values or not text:
            return text
        for v in sorted(self.values, key=len, reverse=True):
            if v:
                text = text.replace(v, "••••")
        return text


def new_card(url: str, page: Any, fields: list[dict], now: float | None = None) -> Card:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    spki = priv.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return Card(login_id=secrets.token_hex(8), origin=origin_of(url), url=url, page=page, fields=fields,
                private_key=priv, public_spki=base64.b64encode(spki).decode(),
                expires=(now or time.time()) + CARD_TTL_S)


def decrypt(card: Card, epk_b64: str, iv_b64: str, ct_b64: str) -> dict[str, str]:
    """她那边的密文 → {字段: 值}。解不开 / login_id 对不上 → 抛 ValueError（不说是哪种，日志里也不带内容）"""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    try:
        epk = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.b64decode(epk_b64))
        shared = card.private_key.exchange(ec.ECDH(), epk)
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=card.login_id.encode(), info=HKDF_INFO).derive(shared)
        plain = AESGCM(key).decrypt(base64.b64decode(iv_b64), base64.b64decode(ct_b64), None)
        body = json.loads(plain.decode("utf-8"))
    except (InvalidTag, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("这张卡的密文解不开（可能是旧卡、或者卡已经作废了）") from exc
    if body.get("login_id") != card.login_id:
        raise ValueError("密文不是这张卡的")
    values = body.get("values") or {}
    keys = {f["key"] for f in card.fields}
    return {k: str(v) for k, v in values.items() if k in keys and isinstance(v, str)}


def encrypt_for(public_spki_b64: str, login_id: str, values: dict[str, str]) -> dict[str, str]:
    """她那边做的事，Python 版（只给测试用，和 shared/desk/login-crypto.js 是同一套）"""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    pub = serialization.load_der_public_key(base64.b64decode(public_spki_b64))
    eph = ec.generate_private_key(ec.SECP256R1())
    shared = eph.exchange(ec.ECDH(), pub)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=login_id.encode(), info=HKDF_INFO).derive(shared)
    iv = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(iv, json.dumps({"login_id": login_id, "values": values}).encode(), None)
    epk = eph.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"epk": base64.b64encode(epk).decode(), "iv": base64.b64encode(iv).decode(),
            "ct": base64.b64encode(ct).decode()}
