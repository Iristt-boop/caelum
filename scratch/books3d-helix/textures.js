// 书脊 / 封面都是 canvas 现画的 —— 共读数据里没有封面图，
// 与其放灰方块，不如用主题色画一套统一装帧。
// 需求文档的规矩：书脊颜色统一，不做来源区分；已读的书只有
// 底部一条极淡的细线。
import * as THREE from "three";

const SPINE_W = 256;
const SPINE_H = 896;
const COVER_W = 460;
const COVER_H = 680;

const hasCJK = (s) => /[\u2e80-\u9fff\uf900-\ufaff\u3040-\u30ff]/.test(s || "");

/** 画布字体栈 —— canvas 认不了 var()，从页面上现读 */
function fontFamily() {
  return getComputedStyle(document.body).fontFamily || "sans-serif";
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/**
 * 一套「装帧」。palette 换主题时重建，所有书脊跟着换皮。
 * 统一风格：同一张渐变、同一种排印，书与书只差文字。
 */
export function createBindery(palette) {
  const font = fontFamily();

  function paintBase(ctx, w, h, { ribs = false } = {}) {
    const g = ctx.createLinearGradient(0, 0, w * 0.9, h);
    g.addColorStop(0, palette.accent);
    g.addColorStop(1, palette.accent2);
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
    // 两侧压暗一点，有「书口」的体积感
    const edge = ctx.createLinearGradient(0, 0, w, 0);
    edge.addColorStop(0, "rgba(0,0,0,0.28)");
    edge.addColorStop(0.12, "rgba(0,0,0,0)");
    edge.addColorStop(0.88, "rgba(0,0,0,0)");
    edge.addColorStop(1, "rgba(0,0,0,0.22)");
    ctx.fillStyle = edge;
    ctx.fillRect(0, 0, w, h);
    if (ribs) {
      // 上下两道垄线 + 一条高光，远看是烫印，近看有装帧
      ctx.fillStyle = "rgba(0,0,0,0.2)";
      ctx.fillRect(w * 0.16, h * 0.075, w * 0.68, h * 0.008);
      ctx.fillRect(w * 0.16, h * 0.895, w * 0.68, h * 0.008);
      ctx.fillStyle = "rgba(255,255,255,0.28)";
      ctx.fillRect(w * 0.16, h * 0.083, w * 0.68, h * 0.004);
      ctx.fillRect(w * 0.16, h * 0.903, w * 0.68, h * 0.004);
      // 左缘一道环境高光，blur 链有东西可拉丝
      const sheen = ctx.createLinearGradient(0, 0, w, 0);
      sheen.addColorStop(0.08, "rgba(255,255,255,0.14)");
      sheen.addColorStop(0.3, "rgba(255,255,255,0)");
      ctx.fillStyle = sheen;
      ctx.fillRect(0, 0, w, h);
      // 细噪点，给抖动和雾化一点咬住的纹理
      for (let i = 0; i < 700; i++) {
        const dark = Math.random() > 0.5;
        ctx.fillStyle = dark ? "rgba(0,0,0,0.05)" : "rgba(255,255,255,0.05)";
        ctx.fillRect(Math.random() * w, Math.random() * h, 1.5, 1.5);
      }
    }
  }

  function fitFont(ctx, text, maxPx, boxW, boxH, vertical) {
    let px = maxPx;
    for (; px > 10; px -= 2) {
      ctx.font = `600 ${px}px ${font}`;
      const len = vertical
        ? ctx.measureText(text).width // 竖排按整串宽度（每字一行时用最大字宽）
        : ctx.measureText(text).width;
      const need = vertical ? Math.max(...[...text].map((ch) => ctx.measureText(ch).width)) : len;
      const height = vertical ? [...text].length * px * 1.18 : px * 1.3;
      if (need <= boxW && height <= boxH) break;
    }
    return px;
  }

  /** 书脊。返回可直接喂给 three 的纹理。 */
  function spine(book, index) {
    const canvas = document.createElement("canvas");
    canvas.width = SPINE_W;
    canvas.height = SPINE_H;
    const ctx = canvas.getContext("2d");

    paintBase(ctx, SPINE_W, SPINE_H, { ribs: true });

    const title = (book.title || "未命名").trim();
    const cjk = hasCJK(title);
    const boxW = SPINE_W * 0.62;
    const boxTop = SPINE_H * 0.09;
    const boxH = SPINE_H * 0.66;

    ctx.fillStyle = palette.onAccent;
    if (cjk) {
      // 中文竖排：自上而下，一列字
      const chars = [...title].slice(0, 14);
      const px = fitFont(ctx, chars.join(""), 64, boxW, boxH, true);
      ctx.font = `600 ${px}px ${font}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const step = px * 1.18;
      const startY = boxTop + (boxH - chars.length * step) / 2 + px * 0.55;
      chars.forEach((ch, i) => ctx.fillText(ch, SPINE_W / 2, startY + i * step));
    } else {
      // 西文旋转 90°，从下往上读
      const px = fitFont(ctx, title, 56, boxH, boxW, false);
      ctx.save();
      ctx.translate(SPINE_W / 2, SPINE_H / 2);
      ctx.rotate(-Math.PI / 2);
      ctx.font = `600 ${px}px ${font}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(title, 0, 0, boxW);
      ctx.restore();
    }

    // 作者在书脊底脚，小字
    if (book.author) {
      ctx.font = `400 24px ${font}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "alphabetic";
      ctx.globalAlpha = 0.72;
      ctx.fillText(book.author, SPINE_W / 2, SPINE_H * 0.86, SPINE_W * 0.8);
      ctx.globalAlpha = 1;
    }

    if (book.complete) {
      // 已读标记：底部一条极淡细线
      ctx.fillStyle = palette.onAccent;
      ctx.globalAlpha = 0.4;
      ctx.fillRect(SPINE_W * 0.3, SPINE_H * 0.945, SPINE_W * 0.4, 3);
      ctx.globalAlpha = 1;
    }

    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 4;
    texture.userData.bookIndex = index;
    return texture;
  }

  /** 详情面板用的大封面。返回 dataURL，不进 WebGL。 */
  function cover(book) {
    const canvas = document.createElement("canvas");
    canvas.width = COVER_W;
    canvas.height = COVER_H;
    const ctx = canvas.getContext("2d");

    paintBase(ctx, COVER_W, COVER_H);

    // 细边框，像一层装帧压线
    ctx.strokeStyle = palette.onAccent;
    ctx.globalAlpha = 0.32;
    ctx.lineWidth = 2;
    roundRect(ctx, 18, 18, COVER_W - 36, COVER_H - 36, 14);
    ctx.stroke();
    ctx.globalAlpha = 1;

    const title = (book.title || "未命名").trim();
    const cjk = hasCJK(title);
    ctx.fillStyle = palette.onAccent;
    ctx.textBaseline = "middle";

    if (cjk) {
      // 竖排大字，偏右上，留白在左下
      const chars = [...title].slice(0, 10);
      let px = 84;
      ctx.textAlign = "center";
      for (; px > 30; px -= 4) {
        ctx.font = `700 ${px}px ${font}`;
        if (chars.length * px * 1.14 <= COVER_H * 0.72) break;
      }
      const step = px * 1.14;
      const x = COVER_W * 0.68;
      const startY = COVER_H * 0.16 + px * 0.6;
      chars.forEach((ch, i) => ctx.fillText(ch, x, startY + i * step));
    } else {
      ctx.textAlign = "left";
      const words = title.split(/\s+/);
      let px = 64;
      let lines = [];
      for (; px > 24; px -= 4) {
        ctx.font = `700 ${px}px ${font}`;
        lines = [];
        let line = "";
        for (const w of words) {
          const next = line ? `${line} ${w}` : w;
          if (ctx.measureText(next).width > COVER_W * 0.72 && line) {
            lines.push(line);
            line = w;
          } else line = next;
        }
        if (line) lines.push(line);
        if (lines.length * px * 1.25 <= COVER_H * 0.6) break;
      }
      lines.slice(0, 5).forEach((line, i) => {
        ctx.fillText(line, COVER_W * 0.14, COVER_H * 0.2 + i * px * 1.25);
      });
    }

    if (book.author) {
      ctx.font = `400 26px ${font}`;
      ctx.textAlign = "left";
      ctx.textBaseline = "alphabetic";
      ctx.globalAlpha = 0.78;
      ctx.fillText(book.author, COVER_W * 0.14, COVER_H * 0.88, COVER_W * 0.7);
      ctx.globalAlpha = 1;
    }

    if (book.complete) {
      ctx.fillStyle = palette.onAccent;
      ctx.globalAlpha = 0.4;
      ctx.fillRect(COVER_W * 0.14, COVER_H * 0.93, COVER_W * 0.2, 4);
      ctx.globalAlpha = 1;
    }

    return canvas.toDataURL("image/png");
  }

  return { spine, cover };
}
