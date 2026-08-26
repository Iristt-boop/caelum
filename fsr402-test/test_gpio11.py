# 临时读数测试：GPIO11（新）+ GPIO13（手）+ GPIO14（昨天）
from machine import ADC, Pin
from time import sleep_ms

adc11 = ADC(Pin(11))
adc11.atten(ADC.ATTN_11DB)
adc13 = ADC(Pin(13))
adc13.atten(ADC.ATTN_11DB)
adc14 = ADC(Pin(14))
adc14.atten(ADC.ATTN_11DB)

print("=== 读数测试 ===")
print("GPIO11(新) | GPIO13(手) | GPIO14(昨天)")
print("")

while True:
    v11 = adc11.read()
    v13 = adc13.read()
    v14 = adc14.read()

    n11 = v11 * 20 // 4095
    bar11 = "=" * n11 + "-" * (20 - n11)

    print("GPIO11(新):{:4d} |{}|   手:{:4d}   昨天:{:4d}".format(v11, bar11, v13, v14))
    sleep_ms(200)
