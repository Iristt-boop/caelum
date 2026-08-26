"""
Super simple: exec() a one-liner in normal REPL, no walrus operator.
"""
import serial
import time

s = serial.Serial("COM4", 115200, timeout=2)
time.sleep(0.3)

# Clean slate
s.write(b"\x02")  # exit raw REPL
time.sleep(0.2)
s.write(b"\r\n\x03\r\n")  # Ctrl-C
time.sleep(0.3)
while s.in_waiting:
    s.read(s.in_waiting)

# Build script - avoid walrus operator for MicroPython compat
lines = [
    "from machine import ADC, Pin",
    "from time import sleep_ms",
    "a = ADC(Pin(14))",
    "a.atten(a.ATTN_11DB)",
    "print('=== FSR402 GPIO14 ===')",
    "for i in range(50):",
    "    v = a.read()",
    "    p = v / 4095.0 * 100.0",
    "    print('raw:{} pressure:{:.1f}%'.format(v, p))",
    "    sleep_ms(200)",
    "print('=== Done ===')",
]
script = "\r\n".join(lines)

# Send it via paste mode (Ctrl-E)
s.write(b"\x05")  # Ctrl-E
time.sleep(0.3)
while s.in_waiting:
    s.read(s.in_waiting)

s.write(script.encode())
time.sleep(0.5)

s.write(b"\x04")  # Ctrl-D execute
time.sleep(15)  # wait for 50 iterations

out = b""
while s.in_waiting:
    out += s.read(s.in_waiting)
text = out.decode("utf-8", errors="replace")
print(text)

# Clean up
s.write(b"\x03\r\n")
time.sleep(0.2)
s.close()
