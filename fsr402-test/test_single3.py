# 诊断：只读 GPIO3 一个传感器，看周期波动还在不在
from machine import ADC, Pin
from time import sleep_ms

adc = ADC(Pin(3))
adc.atten(ADC.ATTN_11DB)

print("=== 单传感器诊断 GPIO3 ===")
print("")

while True:
    v = adc.read()
    n = v * 20 // 4095
    bar = "=" * n + "-" * (20 - n)
    print("GPIO3: {:4d} |{}|".format(v, bar))
    sleep_ms(200)
