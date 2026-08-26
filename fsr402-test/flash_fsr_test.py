"""
Send FSR402 test script to ESP32-S3 via MicroPython REPL.
Uses raw REPL mode (Ctrl-A) for reliable programmatic execution.
"""
import serial
import time
import sys

PORT = "COM4"
BAUD = 115200

# MicroPython script to run on the ESP32
MP_SCRIPT = """\
from machine import ADC, Pin
import time

adc = ADC(Pin(14))
adc.atten(ADC.ATTN_11DB)

print("=== FSR402 ADC Test ===")
print("Pin: GPIO14 (ADC2_CH3)")
print("")

while True:
    raw = adc.read()
    pct = raw / 4095.0 * 100.0

    # Simple bar
    bar_len = int(pct / 5)
    bar = "=" * bar_len + "-" * (20 - bar_len)

    print("raw:{:4d}  pressure:{:5.1f}%  |{}|".format(raw, pct, bar))
    time.sleep(0.2)
"""

def enter_raw_repl(ser):
    """Enter MicroPython raw REPL mode."""
    ser.write(b"\r\n\x03\x03\x03")  # Interrupt any running code
    time.sleep(0.5)
    while ser.in_waiting:
        ser.read(ser.in_waiting)

    ser.write(b"\r\n\x01")  # Ctrl-A enters raw REPL
    time.sleep(0.3)
    # Raw REPL responds with "raw REPL; CTRL-B to exit\r\n>"
    ok = ser.read_until(b">")
    if b"raw REPL" in ok:
        print("[OK] Entered raw REPL mode")
        return True
    print(f"[WARN] Raw REPL response: {ok}")
    return False

def run_script(ser, script: str):
    """Execute a script via raw REPL."""
    # Encode and send
    data = script.encode("utf-8")
    # Command: execute with length
    cmd = f"exec({repr(data)})\r\n".encode()
    ser.write(cmd)
    time.sleep(0.5)

    # Read response until end marker "OK" + 4 bytes
    out = b""
    deadline = time.time() + 5
    while time.time() < deadline:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting)
            out += chunk
            if b"OK" in out[-10:]:
                break
        time.sleep(0.05)
    return out

def soft_reset(ser):
    """Soft reset the board."""
    ser.write(b"\r\n\x03\x03\x03\r\n")
    time.sleep(0.3)
    ser.write(b"\x04")  # Ctrl-D soft reset
    time.sleep(1)
    while ser.in_waiting:
        ser.read(ser.in_waiting)

def main():
    print(f"Opening {PORT} at {BAUD} baud...")
    ser = serial.Serial(PORT, BAUD, timeout=1)
    time.sleep(1)

    # Flush
    while ser.in_waiting:
        ser.read(ser.in_waiting)

    # Enter raw REPL
    if not enter_raw_repl(ser):
        print("Failed to enter raw REPL, trying soft reset...")
        soft_reset(ser)
        time.sleep(2)
        if not enter_raw_repl(ser):
            print("[ERROR] Cannot enter raw REPL. Is something else using COM4?")
            ser.close()
            sys.exit(1)

    # Run the script
    print("Running FSR test script...")
    print("(Press Ctrl+C to stop)\n")

    # For a continuous loop, we use a slightly different approach:
    # Run it without blocking on output
    data = MP_SCRIPT.encode("utf-8")
    cmd = f"exec({repr(data)})\r\n".encode()
    ser.write(cmd)

    # Now just relay all output until user presses Ctrl+C
    try:
        while True:
            if ser.in_waiting:
                chunk = ser.read(ser.in_waiting)
                print(chunk.decode("utf-8", errors="replace"), end="", flush=True)
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\n\nStopping...")
        # Send Ctrl-C to stop the script
        ser.write(b"\x03\x03\x03\r\n")
        time.sleep(0.3)
        ser.close()
        print("Done.")


if __name__ == "__main__":
    main()
