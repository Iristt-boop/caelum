# 临时读数测试：GPIO13（手，新接） + GPIO14（昨天） 对比
from machine import ADC, Pin
from time import sleep_ms

adc13 = ADC(Pin(13))
adc13.atten(ADC.ATTN_11DB)
adc14 = ADC(Pin(14))
adc14.atten(ADC.ATTN_11DB)

print("=== 读数测试 ===")
print("GPIO13 = 手(新接)   |   GPIO14 = 昨天")
print("")

while True:
    v13 = adc13.read()
    v14 = adc14.read()

    n13 = v13 * 20 // 4095
    n14 = v14 * 20 // 4095
    bar13 = "=" * n13 + "-" * (20 - n13)
    bar14 = "=" * n14 + "-" * (20 - n14)

    print("GPIO13(手):{:4d} |{}|   GPIO14(昨天):{:4d} |{}|".format(v13, bar13, v14, bar14))
    sleep_ms(200)
