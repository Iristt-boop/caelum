"""开关她的电脑（2026-10-27，她「先把你能在 N150 开关我的电脑做好」）。

开机：N150 往局域网广播网络唤醒包（magic packet），电脑网卡那边已经开着唤醒。
     不靠 IP —— 电脑是 DHCP，IP 会飘；唤醒包只认 MAC。
关机 / 睡眠 / 取消：ssh 到电脑，用一把**只能干这件事**的钥匙（/root/.ssh/pc_power）。
     电脑那边钥匙带 command= 强制跑 pc-power.ps1，只认 status / shutdown N / sleep / cancel，
     而且只收 N150 的来源地址（scripts/pc-power/setup-pc-power.ps1）。

闸门：关机不出卡（她 09-12 定「能放权就放权」）。真正的那道门在电脑屏幕上 ——
     Windows 会弹 N 秒倒计时，她说「别关」他就 cancel。所以默认给 60 秒，不是 0。

配置（/root/nox-core/.env）：NOX_PC_MAC 必填，没配就不登记这两个工具；
     NOX_PC_USER（Windows 用户名）、NOX_PC_SSH_KEY 可选。
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import time
from typing import Callable

from agent.llm import ToolSpec

logger = logging.getLogger(__name__)

#: 家里的网段（光猫 DHCP 发 192.168.1.x）。找电脑当前 IP 时往这个段里敲一遍门
LAN_PREFIX = "192.168.1."
BROADCASTS = ("192.168.1.255", "255.255.255.255")


STATUS_SPEC = ToolSpec(
    side_effect="read",
    name="pc_status",
    description=(
        "看她的电脑（家里那台台式机）开着没有：开着的话什么时候开的机、她登没登录、锁没锁屏。"
        "开机 / 关机之前先看一眼。"
    ),
    parameters={"type": "object", "properties": {}},
)

POWER_SPEC = ToolSpec(
    side_effect="irreversible",
    confirm_via="关机前她的电脑屏幕上弹倒计时（默认 60 秒），她说别关就调 cancel",
    name="pc_power",
    description=(
        "开关她的电脑。action：\n"
        "- wake：开机（发网络唤醒包，等它连上网，最多等一分钟左右）\n"
        "- shutdown：关机。电脑上会弹 delay 秒倒计时（默认 60）——她正用着电脑的时候"
        "**先问她**，没保存的东西会丢\n"
        "- sleep：睡眠（10 秒后睡，开回来快、东西都还在；她只是暂时离开优先用这个）\n"
        "- cancel：取消正在倒计时的关机 / 睡眠\n"
        "电脑在她要远程干活、或者她出门忘关的时候用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["wake", "shutdown", "sleep", "cancel"]},
            "delay": {
                "type": "integer", "minimum": 0, "maximum": 600,
                "description": "shutdown 的倒计时秒数，默认 60",
            },
        },
        "required": ["action"],
    },
)


def magic_packet(mac: str) -> bytes:
    """6 个 0xFF + MAC 重复 16 遍。"""
    raw = bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", mac))
    if len(raw) != 6:
        raise ValueError(f"MAC 地址不对：{mac!r}")
    return b"\xff" * 6 + raw * 16


def _norm_mac(mac: str) -> str:
    return re.sub(r"[^0-9a-f]", "", mac.lower())


class PcPower:
    """拆成可注入的几块（run / sender / sleep），测试不碰真网络。"""

    def __init__(
        self,
        mac: str,
        user: str = "14372",
        key: str = "/root/.ssh/pc_power",
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
        wake_wait: float = 75.0,
    ):
        self.mac = mac
        self.user = user
        self.key = key
        self.run = run
        self.sleep = sleep
        self.wake_wait = wake_wait

    # ---------- 找到它 ----------

    def _neigh_ip(self) -> str | None:
        """邻居表里按 MAC 找 IP（FAILED / INCOMPLETE 的不算）。"""
        out = self.run(["ip", "-4", "neigh", "show"], capture_output=True, text=True, timeout=5).stdout
        want = _norm_mac(self.mac)
        for line in out.splitlines():
            parts = line.split()
            if "lladdr" in parts and _norm_mac(parts[parts.index("lladdr") + 1]) == want:
                if parts[-1] not in ("FAILED", "INCOMPLETE"):
                    return parts[0]
        return None

    def _knock_lan(self) -> None:
        """往整个网段各发一个 UDP 小包，逼内核把 ARP 问一遍，邻居表就有它了。"""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setblocking(False)
            for i in range(1, 255):
                try:
                    s.sendto(b"", (f"{LAN_PREFIX}{i}", 9))
                except OSError:
                    pass

    def find_ip(self) -> str | None:
        ip = self._neigh_ip()
        if ip and self._alive(ip):
            return ip
        self._knock_lan()
        self.sleep(1.5)
        ip = self._neigh_ip()
        return ip if ip and self._alive(ip) else None

    def _alive(self, ip: str) -> bool:
        r = self.run(["ping", "-c1", "-W1", ip], capture_output=True, timeout=5)
        return r.returncode == 0

    # ---------- 跟它说话 ----------

    def _ssh(self, ip: str, verb: str) -> dict:
        r = self.run(
            ["ssh", "-i", self.key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
             # 按别名钉主机钥匙：IP 会飘，钥匙不该跟着 IP 走
             "-o", "HostKeyAlias=tangtang-pc", "-o", "StrictHostKeyChecking=accept-new",
             f"{self.user}@{ip}", verb],
            capture_output=True, text=True, timeout=20,
        )
        out = (r.stdout or "").strip().lstrip("\ufeff")
        try:
            data = json.loads(out.splitlines()[-1]) if out else {}
        except (json.JSONDecodeError, IndexError):
            data = {}
        if not data:
            err = (r.stderr or "").strip()[-200:]
            logger.warning("pc_power ssh %s 没拿到回包：rc=%s err=%s out=%s", verb, r.returncode, err, out[-200:])
            return {"ok": False, "error": err or out or f"rc={r.returncode}"}
        return data

    # ---------- 动作 ----------

    def status(self) -> str:
        ip = self.find_ip()
        if not ip:
            return "她的电脑现在是关着的（局域网里找不到它）。"
        d = self._ssh(ip, "status")
        if not d.get("ok"):
            return f"电脑开着（{ip}），但问不到详情：{d.get('error')}"
        who = d.get("user") or "没人登录"
        lock = "，锁屏中" if d.get("locked") else ""
        return f"电脑开着（{ip}），{d.get('boot', '?')} 开的机；登录的是 {who}{lock}。"

    def wake(self) -> str:
        if self.find_ip():
            return "电脑本来就开着，不用唤醒。"
        pkt = magic_packet(self.mac)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for _ in range(3):
                for addr in BROADCASTS:
                    for port in (9, 7):
                        s.sendto(pkt, (addr, port))
        logger.info("pc_power: 唤醒包已发（%s）", self.mac)
        waited = 0.0
        while waited < self.wake_wait:
            self.sleep(5)
            waited += 5
            ip = self.find_ip()
            if ip:
                return f"唤醒包发出去了，电脑 {int(waited)} 秒后连上了网（{ip}），在开机。"
        return (f"唤醒包发出去了，但 {int(self.wake_wait)} 秒内没等到电脑上网。"
                "可能还在开机，也可能没醒 —— 过一会儿 pc_status 再看，别说已经开了。")

    def power(self, action: str, delay: int = 60) -> str:
        if action == "wake":
            return self.wake()
        ip = self.find_ip()
        if not ip:
            return "电脑已经是关着的，没什么可关 / 可取消的。" if action != "cancel" else "电脑是关着的，没有要取消的。"
        if action == "shutdown":
            delay = max(0, min(int(delay), 600))
            d = self._ssh(ip, f"shutdown {delay}")
            if d.get("ok"):
                return f"好，电脑 {delay} 秒后关机，屏幕上弹了倒计时。她说别关就马上 cancel。"
            return f"关机没发成：{d.get('error') or d}"
        if action == "sleep":
            d = self._ssh(ip, "sleep")
            return "电脑 10 秒后睡眠。" if d.get("ok") else f"睡眠没发成：{d.get('error') or d}"
        if action == "cancel":
            d = self._ssh(ip, "cancel")
            return "取消了，电脑不关了。" if d.get("ok") else f"取消没成功：{d.get('error') or d}"
        return f"不认识的动作：{action}"


def register_all(loop, pc: PcPower) -> None:
    """两个工具，顺序固定（工具定义是缓存前缀的一部分）。"""
    loop.register(STATUS_SPEC, lambda args: pc.status())
    def power(args: dict) -> str:
        delay = args.get("delay")
        return pc.power(str(args.get("action", "")), 60 if delay is None else int(delay))

    loop.register(POWER_SPEC, power)


def from_env() -> PcPower | None:
    mac = os.environ.get("NOX_PC_MAC", "").strip()
    if not mac:
        return None
    return PcPower(
        mac,
        user=os.environ.get("NOX_PC_USER", "14372").strip() or "14372",
        key=os.environ.get("NOX_PC_SSH_KEY", "/root/.ssh/pc_power").strip() or "/root/.ssh/pc_power",
    )
