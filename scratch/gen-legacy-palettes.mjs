// 一次性转写工具：把 frontend/src/themes.js 里 8 套旧主题的 derive() 最终值，
// 转成共享真源 palettes.css 的 [data-theme] 块（含 App 过渡别名桥）。
// 用完即弃，不留依赖 —— 真源落地后这段逻辑就没有存在意义了。
import { THEMES } from "../nox-app/frontend/src/themes.js";
import { writeFileSync } from "node:fs";

const hexToRgb = (h) => {
  const s = h.replace("#", "");
  return { r: parseInt(s.slice(0, 2), 16), g: parseInt(s.slice(2, 4), 16), b: parseInt(s.slice(4, 6), 16) };
};
const rgba = (hex, a) => {
  const { r, g, b } = hexToRgb(hex);
  return `rgba(${r}, ${g}, ${b}, ${a})`;
};
const mixWhite = (hex, ratio) => {
  const { r, g, b } = hexToRgb(hex);
  const c = (v) => Math.round(v + (255 - v) * ratio).toString(16).padStart(2, "0");
  return `#${c(r)}${c(g)}${c(b)}`;
};
const darken = (hex, amt) => {
  const { r, g, b } = hexToRgb(hex);
  const c = (v) => Math.max(0, v - amt).toString(16).padStart(2, "0");
  return `#${c(r)}${c(g)}${c(b)}`;
};

const DANGER = "#C2654E";

// 别名桥：新令牌 → 旧 --color-* 名。等价的新令牌用 var() 引用，
// 没有对应物的保留旧字面量。页面全部迁移完后整段删除。
function bridge(v, opts = {}) {
  const {
    accentLightLiteral, accentTextLiteral, successLiteral,
    darkBtn, // 新主题传 "accent"，旧主题传字面量
    accentBgLiteral, // 旧主题保留原字面量（老的 accent-bg 是实心深色调，与规范的半透明 soft 不同）
  } = opts;
  const warn = darkBtn === "var(--accent)"
    ? " /* ⚠️ 新主题下按钮里写死的白字对比度欠佳，属已知过渡态，页面迁移时换成 pill 组件 */"
    : "";
  return [
    "  /* ---- App 过渡别名桥（未迁移页面靠它认新令牌；全迁完后删除）---- */",
    `  --color-accent: var(--accent);`,
    `  --color-accent-light: ${accentLightLiteral};`,
    `  --color-accent-bg: ${accentBgLiteral};`,
    `  --color-accent-text: ${accentTextLiteral};`,
    `  --color-bg: var(--bg-base);`,
    `  --color-card: var(--surface);`,
    `  --color-bubble-ai: var(--panel-2);`,
    `  --color-text-primary: var(--text-1);`,
    `  --color-text-body: var(--text-1);`,
    `  --color-text-secondary: var(--text-2);`,
    `  --color-text-muted: var(--text-3);`,
    `  --color-text-subtle: var(--text-3);`,
    `  --color-text-dark: var(--text-2);`,
    `  --color-border: var(--line);`,
    `  --color-border-light: var(--line);`,
    `  --color-dark-btn: ${darkBtn};${warn}`,
    `  --color-success: ${successLiteral};`,
    `  --color-danger: ${DANGER};`,
  ].join("\n");
}

let out = [];

for (const [key, t] of Object.entries(THEMES)) {
  const v = t.vars;
  const bg = v["--color-bg"];
  const card = v["--color-card"];
  const acc = v["--color-accent"];
  const succ = v["--color-success"];
  const t1 = v["--color-text-primary"];
  const t2 = v["--color-text-secondary"];
  const t3 = v["--color-text-muted"];
  const line = v["--color-border"];
  const isDark = key === "dark";

  out.push(`/* ---------- ${t.name} ${key} · App 遗产主题 ----------`);
  out.push(`   由原 themes.js derive() 最终值原样转写，观感与改造前一致。 */`);
  out.push(`[data-theme="${key}"] {`);
  out.push(`  --bg-base: ${bg};`);
  out.push(`  --rail: ${rgba(card, 0.8)};`);
  out.push(`  --panel: ${rgba(card, 0.62)};`);
  out.push(`  --panel-2: ${rgba(card, 0.4)};`);
  out.push(`  --line: ${line};`);
  out.push(`  --text-1: ${t1};`);
  out.push(`  --text-2: ${t2};`);
  out.push(`  --text-3: ${t3};`);
  out.push(`  --accent: ${acc};`);
  out.push(`  --accent-2: ${succ};`);
  out.push(`  --accent-soft: ${rgba(acc, 0.15)};`);
  out.push(`  --shadow: ${isDark ? "0 18px 48px -20px rgba(0, 0, 0, 0.7)" : "0 14px 34px -16px rgba(40, 30, 20, 0.22)"};`);
  out.push(`  --glow-a: ${rgba(acc, 0.32)};`);
  out.push(`  --glow-b: ${rgba(succ, 0.26)};`);
  out.push(`  --glow-c: ${rgba(t3, 0.22)};`);
  out.push("");
  out.push(`  /* 卡片/面板的不透明底：沿用原 --color-card 值 */`);
  out.push(`  --surface: ${card};`);
  out.push("");
  out.push(bridge(v, {
    accentLightLiteral: v["--color-accent-light"],
    accentTextLiteral: v["--color-accent-text"],
    successLiteral: succ,
    darkBtn: v["--color-dark-btn"],
    accentBgLiteral: v["--color-accent-bg"],
  }));
  out.push(`}`);
  out.push("");
}

// 四套新主题补别名桥：核心段在文件上半部手写区，别名按同名第二规则追加（后规则并入，不覆盖）
for (const id of ["sunset", "deepspace", "rainbow", "dreamy"]) {
  const accents = { sunset: "#ff9d6e", deepspace: "#a78bfa", rainbow: "#7b6ef0", dreamy: "#c49ad9" };
  const seconds = { sunset: "#f07ba8", deepspace: "#6ea8ff", rainbow: "#ef7fae", dreamy: "#f6a5c8" };
  const acc = accents[id], succ = seconds[id];
  out.push(`/* ${id} 的别名桥（核心段在上面手写区）*/`);
  out.push(`[data-theme="${id}"] {`);
  out.push(bridge(null, {
    accentLightLiteral: mixWhite(acc, 0.4),
    accentTextLiteral: darken(acc, 30),
    successLiteral: succ,
    darkBtn: "var(--accent)",
    accentBgLiteral: "var(--accent-soft)",
  }));
  out.push(`}`);
  out.push("");
}

writeFileSync("legacy-themes.frag", out.join("\n"), "utf8");
console.log("written", out.join("\n").length, "chars,", Object.keys(THEMES).length, "themes");
