import io

# ---------- palette.js：主色提取 + 对比文字色 ----------
p = "palette.js"
s = io.open(p, encoding="utf-8").read()
s += '''
/** 亮度（0..1），定文字用深还是浅 */
export function lumOf(hex) {
  const [r, g, b] = hexToRGB(hex);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** 这个颜色上放文字，深底配亮字、浅底配暗字 */
export function onColorFor(hex) {
  return lumOf(hex) < 0.45 ? shade(hex, 0.86) : shade(hex, -0.74);
}

/**
 * 从封面 img 里提主色：画到 16x16 取像素，饱和度当权重
 * （免得整张平均成土色），纯黑纯白跳过。跨域污染/失败返回 null。
 */
export function extractDominantColor(img) {
  try {
    const c = document.createElement("canvas");
    c.width = 16;
    c.height = 16;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, 16, 16);
    const d = ctx.getImageData(0, 0, 16, 16).data;
    let r = 0, g = 0, b = 0, w = 0;
    for (let i = 0; i < d.length; i += 4) {
      const pr = d[i] / 255, pg = d[i + 1] / 255, pb = d[i + 2] / 255;
      const max = Math.max(pr, pg, pb), min = Math.min(pr, pg, pb);
      const sat = max === 0 ? 0 : (max - min) / max;
      const lum = 0.2126 * pr + 0.7152 * pg + 0.0722 * pb;
      if (lum > 0.97 || lum < 0.03) continue;
      const weight = 0.15 + 0.85 * sat;
      r += pr * weight; g += pg * weight; b += pb * weight; w += weight;
    }
    if (!w) return null;
    return rgbToHex([r / w, g / w, b / w]);
  } catch {
    return null;
  }
}
'''
io.open(p, "w", encoding="utf-8", newline="\n").write(s)

# ---------- BookShelf.jsx：书脊吃主色 ----------
p = "BookShelf.jsx"
s = io.open(p, encoding="utf-8").read()
s = s.replace(
    'import { libraryCoverUrl } from "../../lib/api";',
    'import { libraryCoverUrl } from "../../lib/api";\nimport { extractDominantColor, onColorFor, shade } from "./palette";',
)
s = s.replace(
    """  // 真封面优先：bridge 静态 404（没抓到）就回退 canvas 生成装帧
  const [realFailed, setRealFailed] = useState(false);""",
    """  // 真封面优先：bridge 静态 404（没抓到）就回退 canvas 生成装帧
  const [realFailed, setRealFailed] = useState(false);
  // 书脊跟封面同色：封面加载后提主色，书脊/顶面/封底全部换过去
  const [spineColor, setSpineColor] = useState(null);
  const onCoverLoad = (e) => {
    if (spineColor) return;
    const c = extractDominantColor(e.currentTarget);
    if (c) setSpineColor(c);
  };""",
)
s = s.replace(
    """            onError={() => setRealFailed(true)}
            className="pointer-events-none absolute inset-0 h-full w-full rounded-[3px] object-cover"
            style={{ backfaceVisibility: "hidden" }}""",
    """            onError={() => setRealFailed(true)}
            onLoad={onCoverLoad}
            crossOrigin="anonymous"
            className="pointer-events-none absolute inset-0 h-full w-full rounded-[3px] object-cover"
            style={{ backfaceVisibility: "hidden" }}""",
)
s = s.replace(
    """            transform: "rotateY(-90deg)",
            background: "linear-gradient(168deg, var(--accent), var(--accent-2))",
          }}""",
    """            transform: "rotateY(-90deg)",
            background: spineColor
              ? `linear-gradient(168deg, ${shade(spineColor, -0.08)}, ${shade(spineColor, 0.32)})`
              : "linear-gradient(168deg, var(--accent), var(--accent-2))",
            transition: "background .5s ease",
          }}""",
)
s = s.replace(
    """                color: "var(--bg-base)",
                maxHeight: "74%",""",
    """                color: spineColor ? onColorFor(spineColor) : "var(--bg-base)",
                maxHeight: "74%",""",
)
s = s.replace(
    """                    color: "var(--bg-base)",
                    opacity: 0.7,
                    maxHeight: 88,""",
    """                    color: spineColor ? onColorFor(spineColor) : "var(--bg-base)",
                    opacity: 0.7,
                    maxHeight: 88,""",
)
s = s.replace(
    """                    borderRadius: 1,
                    background: "var(--bg-base)",
                    opacity: 0.45,""",
    """                    borderRadius: 1,
                    background: spineColor ? onColorFor(spineColor) : "var(--bg-base)",
                    opacity: 0.45,""",
)
s = s.replace(
    """            transform: "rotateX(-90deg)",
            background: "color-mix(in srgb, var(--accent) 18%, var(--bg-base))",""",
    """            transform: "rotateX(-90deg)",
            background: spineColor
              ? `linear-gradient(to bottom, ${shade(spineColor, 0.25)}, ${shade(spineColor, 0.05)})`
              : "color-mix(in srgb, var(--accent) 18%, var(--bg-base))",""",
)
s = s.replace(
    """            transform: `translateZ(${-spec.t}px)`,
            background: "color-mix(in srgb, var(--accent) 30%, var(--bg-base))",""",
    """            transform: `translateZ(${-spec.t}px)`,
            background: spineColor ? shade(spineColor, -0.32) : "color-mix(in srgb, var(--accent) 30%, var(--bg-base))",""",
)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)

# ---------- BookDetail.jsx：整页底色也吃封面主色 ----------
p = "BookDetail.jsx"
s = io.open(p, encoding="utf-8").read()
s = s.replace(
    'import { libraryCoverUrl } from "../../lib/api";',
    'import { libraryCoverUrl } from "../../lib/api";\nimport { extractDominantColor, lumOf, onColorFor } from "./palette";',
)
s = s.replace(
    "  const tone = palette; // 进来前已经按这本书 shift 过的调色盘",
    """  const tone = palette; // 进来前已经按这本书 shift 过的调色盘
  // 封面加载后提主色：整页背景、书口、文字对比全部跟着真封面走
  const [coverColor, setCoverColor] = useState(null);
  const base = coverColor || tone.accent;
  const bgText = coverColor ? onColorFor(coverColor) : tone.onAccent;""",
)
s = s.replace(
    "  const bgText = tone.onAccent;\n\n",
    "",
)
s = s.replace(
    "        background: `linear-gradient(135deg, ${tone.accent}, ${tone.accent2})`,",
    "        background: `linear-gradient(135deg, ${base}, ${shade(base, lumOf(base) < 0.5 ? 0.14 : -0.16)})`,",
)
s = s.replace(
    "  const pages = shade(tone.accent, 0.8);\n  const pagesLine = shade(tone.accent, -0.18);",
    "  const pages = shade(base, 0.8);\n  const pagesLine = shade(base, -0.18);",
)
s = s.replace(
    "background: shade(tone.accent, -0.3) }}",
    "background: shade(base, -0.3) }}",
)
s = s.replace(
    "linear-gradient(to right, ${pages}, ${shade(tone.accent, 0.66)})",
    "linear-gradient(to right, ${pages}, ${shade(base, 0.66)})",
)
s = s.replace(
    "                background: shade(tone.accent, 0.6),",
    "                background: shade(base, 0.6),",
)
s = s.replace(
    """              src={coverUrl.current}
              onError={() => setRealFailed(true)}""",
    """              src={coverUrl.current}
              onError={() => setRealFailed(true)}
              onLoad={(e) => {
                if (coverColor) return;
                const c = extractDominantColor(e.currentTarget);
                if (c) setCoverColor(c);
              }}
              crossOrigin="anonymous\"""",
)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("ok")
