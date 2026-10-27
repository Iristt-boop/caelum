"""开关她的电脑（tools/pc_power.py）。不碰真网络：run / sleep 注入，socket 发送打桩。"""

from __future__ import annotations

import subprocess

import pytest

from tools import pc_power
from tools.pc_power import PcPower, magic_packet

MAC = "08-BF-B8-8B-E9-2A"


class FakeNet:
    """假的 ip neigh / ping / ssh。online 决定电脑在不在；ssh_out 是电脑那边的回包。"""

    def __init__(self, online=True, ssh_out='{"ok":true}', neigh_mac="08:bf:b8:8b:e9:2a"):
        self.online = online
        self.ssh_out = ssh_out
        self.neigh_mac = neigh_mac
        self.ssh_calls: list[list[str]] = []

    def __call__(self, argv, **kw):
        if argv[:2] == ["ip", "-4"]:
            out = (f"192.168.1.19 dev eth0 lladdr {self.neigh_mac} REACHABLE\n"
                   "192.168.1.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n") if self.online else ""
            return subprocess.CompletedProcess(argv, 0, out, "")
        if argv[0] == "ping":
            return subprocess.CompletedProcess(argv, 0 if self.online else 1, "", "")
        if argv[0] == "ssh":
            self.ssh_calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, self.ssh_out, "")
        raise AssertionError(argv)


@pytest.fixture(autouse=True)
def no_real_packets(monkeypatch):
    sent = []

    class Sock:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def setblocking(self, *a): pass
        def setsockopt(self, *a): pass
        def sendto(self, data, addr): sent.append((data, addr))

    monkeypatch.setattr(pc_power.socket, "socket", Sock)
    return sent


def test_唤醒包是6个FF加MAC重复16遍():
    p = magic_packet(MAC)
    assert len(p) == 102
    assert p[:6] == b"\xff" * 6
    assert p[6:12] == bytes.fromhex("08bfb88be92a")
    assert p[6:] == bytes.fromhex("08bfb88be92a") * 16


def test_MAC写错直接报错():
    with pytest.raises(ValueError):
        magic_packet("08-BF-B8")


def test_关着的电脑_唤醒会广播唤醒包并等它上网(no_real_packets):
    net = FakeNet(online=False)
    naps = []

    def nap(s):
        naps.append(s)
        if len(naps) >= 4:          # 敲门那次 + 等了几轮之后，它醒了
            net.online = True

    pc = PcPower(MAC, run=net, sleep=nap)
    out = pc.wake()
    assert "连上了网" in out and "192.168.1.19" in out
    wol = [(d, a) for d, a in no_real_packets if len(d) == 102]
    assert wol and all(d == magic_packet(MAC) for d, _ in wol)
    assert {a[0] for _, a in wol} == {"192.168.1.255", "255.255.255.255"}


def test_一直没醒_不许说已经开了():
    net = FakeNet(online=False)
    pc = PcPower(MAC, run=net, sleep=lambda s: None, wake_wait=10)
    out = pc.wake()
    assert "没等到" in out and "别说已经开了" in out
    assert "连上了网" not in out


def test_开着的电脑不再发唤醒包(no_real_packets):
    pc = PcPower(MAC, run=FakeNet(online=True), sleep=lambda s: None)
    assert "本来就开着" in pc.wake()
    assert not [d for d, _ in no_real_packets if len(d) == 102]


def test_关机走专用钥匙_带倒计时():
    net = FakeNet(online=True, ssh_out='{"ok":true,"delay":60}')
    pc = PcPower(MAC, run=net, sleep=lambda s: None, key="/root/.ssh/pc_power")
    out = pc.power("shutdown")
    assert "60 秒后关机" in out
    argv = net.ssh_calls[-1]
    assert argv[-1] == "shutdown 60"
    assert argv[argv.index("-i") + 1] == "/root/.ssh/pc_power"
    assert "14372@192.168.1.19" in argv
    assert "HostKeyAlias=tangtang-pc" in argv


def test_关机倒计时被夹在0到600():
    net = FakeNet(online=True)
    pc = PcPower(MAC, run=net, sleep=lambda s: None)
    pc.power("shutdown", 99999)
    assert net.ssh_calls[-1][-1] == "shutdown 600"
    pc.power("shutdown", -5)
    assert net.ssh_calls[-1][-1] == "shutdown 0"


def test_工具层_delay给0就是0_不给就是60():
    net = FakeNet(online=True)
    pc = PcPower(MAC, run=net, sleep=lambda s: None)
    handlers = {}

    class Loop:
        def register(self, spec, h): handlers[spec.name] = h

    pc_power.register_all(Loop(), pc)
    handlers["pc_power"]({"action": "shutdown", "delay": 0})
    assert net.ssh_calls[-1][-1] == "shutdown 0"
    handlers["pc_power"]({"action": "shutdown"})
    assert net.ssh_calls[-1][-1] == "shutdown 60"


def test_电脑那边拒了_不许说关上了():
    net = FakeNet(online=True, ssh_out='{"ok":false,"error":"unknown command"}')
    pc = PcPower(MAC, run=net, sleep=lambda s: None)
    out = pc.power("shutdown")
    assert "没发成" in out and "秒后关机" not in out


def test_ssh连不上_没回包也不许说成功():
    net = FakeNet(online=True, ssh_out="")
    pc = PcPower(MAC, run=net, sleep=lambda s: None)
    assert "没发成" in pc.power("shutdown")
    assert "没成功" in pc.power("cancel")


def test_关着的电脑不去ssh():
    net = FakeNet(online=False)
    pc = PcPower(MAC, run=net, sleep=lambda s: None)
    assert "关着" in pc.power("shutdown")
    assert "关着" in pc.status()
    assert net.ssh_calls == []


def test_邻居表里别的MAC不算():
    net = FakeNet(online=True, neigh_mac="11:22:33:44:55:66")
    pc = PcPower(MAC, run=net, sleep=lambda s: None)
    assert "关着" in pc.status()


def test_状态带上登录和锁屏():
    net = FakeNet(online=True, ssh_out='﻿{"ok":true,"boot":"2026-10-27T12:37:20","user":"PC\\\\14372","locked":true}')
    pc = PcPower(MAC, run=net, sleep=lambda s: None)
    out = pc.status()
    assert "12:37:20" in out and "14372" in out and "锁屏" in out


def test_声明_关机是撤不回来的_开机前先看是只读():
    assert pc_power.POWER_SPEC.side_effect == "irreversible"
    assert pc_power.POWER_SPEC.confirm_via
    assert pc_power.STATUS_SPEC.side_effect == "read"


def test_没配MAC就不登记(monkeypatch):
    monkeypatch.delenv("NOX_PC_MAC", raising=False)
    assert pc_power.from_env() is None
    monkeypatch.setenv("NOX_PC_MAC", MAC)
    assert pc_power.from_env().mac == MAC
