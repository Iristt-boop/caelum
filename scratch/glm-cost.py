M = 1_000_000
# 真实 7 天用量（bridge usage_log，2026-09-06 查）
peak = dict(hit=3_632_512, miss=2_071_020, out=39_407)
off = dict(hit=12_199_680, miss=9_593_371, out=123_851)

# deepseek-v4-flash 现行峰谷价 USD / 百万 token（2026-08-17 起）
DS = {"peak": dict(hit=0.014, miss=0.44, out=1.32),
      "off": dict(hit=0.007, miss=0.22, out=0.66)}
# GLM-5.3-Flash 单一价（无峰谷）
GLM = dict(hit=0.03, miss=0.15, out=0.50)


def cost(u, p):
    return (u["hit"] * p["hit"] + u["miss"] * p["miss"] + u["out"] * p["out"]) / M


ds = cost(peak, DS["peak"]) + cost(off, DS["off"])
glm = cost(peak, GLM) + cost(off, GLM)

H = peak["hit"] + off["hit"]
MI = peak["miss"] + off["miss"]
O = peak["out"] + off["out"]

print("7 天真实用量：命中输入 %d | 未命中 %d | 输出 %d" % (H, MI, O))
print("总命中率 %.1f%%" % (100.0 * H / (H + MI)))
print()
print("  deepseek-v4-flash  $%.4f  （约 CNY %.2f / 7 天）" % (ds, ds * 7.1))
print("  glm-5.3-flash      $%.4f  （约 CNY %.2f / 7 天）" % (glm, glm * 7.1))
print("  → GLM 是 DeepSeek 的 %.2f 倍" % (glm / ds))
print()
print("拆开看：")
for name, u, tag in (("高峰", peak, "peak"), ("空闲", off, "off")):
    d, g = cost(u, DS[tag]), cost(u, GLM)
    print("  %s段  DS $%.4f  vs  GLM $%.4f  (%.2fx)" % (name, d, g, g / d))
print()
ds_hit = peak["hit"] * DS["peak"]["hit"] / M + off["hit"] * DS["off"]["hit"] / M
ds_miss = peak["miss"] * DS["peak"]["miss"] / M + off["miss"] * DS["off"]["miss"] / M
ds_out = peak["out"] * DS["peak"]["out"] / M + off["out"] * DS["off"]["out"] / M
print("三项各自（7 天，USD）：")
print("  缓存命中  DS $%.4f  →  GLM $%.4f  (%.1fx)" % (ds_hit, H * GLM["hit"] / M, (H * GLM["hit"] / M) / ds_hit))
print("  未命中    DS $%.4f  →  GLM $%.4f  (%.2fx)" % (ds_miss, MI * GLM["miss"] / M, (MI * GLM["miss"] / M) / ds_miss))
print("  输出      DS $%.4f  →  GLM $%.4f  (%.2fx)" % (ds_out, O * GLM["out"] / M, (O * GLM["out"] / M) / ds_out))
