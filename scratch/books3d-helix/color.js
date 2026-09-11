// The composite writes straight to the default framebuffer with no automatic
// output conversion, so anything it mixes in — background, dither palettes —
// has to arrive already in display space. THREE.Color would convert hex to
// linear on the way in, which is exactly what we don't want here, so unpack the
// components by hand into a plain Vector3.
import * as THREE from "three";

export function hexToSRGB(hex) {
  const n = parseInt(hex.slice(1), 16);
  return new THREE.Vector3(
    ((n >> 16) & 255) / 255,
    ((n >> 8) & 255) / 255,
    (n & 255) / 255
  );
}

// ---------------------------------------------------------------- Caelum 扩展
//
// 参考仓库的颜色是写死的 hex；这里全部换成运行时从 CSS 变量读出来的主题色。
// DESIGN.md 的硬规矩：不许有写死的颜色 —— WebGL 里的颜色也一样是变量。

/** #rrggbb -> [r,g,b] 0..1。只接受六位 hex（主题真源 palettes.css 里都是 hex）。 */
export function hexToRGB(hex) {
  const n = parseInt(hex.replace("#", ""), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => v / 255);
}

export function rgbToHex([r, g, b]) {
  const c = (v) => Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16).padStart(2, "0");
  return `#${c(r)}${c(g)}${c(b)}`;
}

/** f < 0 变暗（乘法），f > 0 变亮（向白混合）。返回 hex。 */
export function shade(hex, f) {
  const [r, g, b] = hexToRGB(hex);
  if (f < 0) {
    const k = 1 + f;
    return rgbToHex([r * k, g * k, b * k]);
  }
  return rgbToHex([r + (1 - r) * f, g + (1 - g) * f, b + (1 - b) * f]);
}

/** 从当前主题里取这一页要用的颜色。CSS 变量是唯一真源。 */
export function readThemeColors() {
  const s = getComputedStyle(document.documentElement);
  const v = (name, fallback) => (s.getPropertyValue(name) || fallback).trim();
  return {
    bg: v("--bg-base", "#1a1220"),
    text: v("--text-1", "#fdeef0"),
    accent: v("--accent", "#ff9d6e"),
    accent2: v("--accent-2", "#f07ba8"),
  };
}
