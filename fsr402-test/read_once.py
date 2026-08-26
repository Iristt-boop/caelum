# 软复位 + 读 N 秒串口输出（一次性，读完退出）
import serial
import time
import sys

PORT = "COM4"
BAUD = 115200
SECONDS = 30

s = serial.Serial(PORT, BAUD, timeout=1)
time.sleep(0.3)

# 中断 + 软复位（软复位后 MicroPython 会自动跑 main.py）
s.write(b"\r\n\x03\r\n")
time.sleep(0.2)
while s.in_waiting:
    s.read(s.in_waiting)
s.write(b"\x04")
time.sleep(2)

end = time.time() + SECONDS
while time.time() < end:
    if s.in_waiting:
        chunk = s.read(s.in_waiting)
        sys.stdout.buffer.write(chunk)
        sys.stdout.flush()
    time.sleep(0.02)

# 停止脚本，回到 REPL
s.write(b"\r\n\x03\r\n")
time.sleep(0.2)
s.close()
print("\n--- 读数结束 ---")
