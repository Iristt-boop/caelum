// 移植自 dunyaochen/-3D-（MIT，原作 Yousuf Soomro），按 Caelum 的需求改造：
//
// 1. **池化虚拟滚动** —— 原版把所有卡片一次性铺进场景；藏书是 500+ 本，
//    这里只建 `config.pool` 个网格，shader 里 uCount = 池深，
//    槽位循环照旧，书和网格的对应关系每帧重算 —— 网格滑出窗口时
//    换上另一本书的纹理，GPU 里的东西永远是同一批。
// 2. **纹理是 canvas 现画的**（textures.js），没有异步加载，
//    进场动画不用等图。
// 3. **颜色全部来自主题**（applyTheme），不再有写死的 hex。
// 4. lil-gui 调参面板删了；点击书脊 = 聚焦 + 通知 React 开详情面板。

import * as THREE from "three";
import { config } from "./config.js";
import { cardVertex, cardFragment } from "./shaders/card.js";
import { createScrollController } from "./scroll.js";
import { createPostPipeline } from "./post.js";
import { createCardBuffer } from "./cardbuffer.js";
import { createTrail } from "./trail.js";
import { hexToSRGB } from "./color.js";
import { createBindery } from "./textures.js";

const BEND_WEIGHTS = {
  vertical: [1, 0],
  horizontal: [0, 1],
  both: [0.7, 0.7],
};

function approach(current, target, cfg) {
  const rate = target > current ? cfg.hoverInEase : cfg.hoverOutEase;
  return current + (target - current) * rate;
}

function entryAspect() {
  const aspect = config.cardWidth / config.cardHeight;
  return 1 + (aspect - 1) * config.entryRound;
}

function smoothstep(edge0, edge1, x) {
  if (edge1 <= edge0) return x <= edge0 ? 0 : 1;
  const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

const mod = (n, m) => ((n % m) + m) % m;

export function createCarousel(canvas, books, palette, hooks = {}) {
  const { onCenterChange, onOpen } = hooks;
  const count = books.length;
  const pool = Math.min(config.pool, Math.max(count, 1));

  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: false,
    powerPreference: "high-performance",
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(config.fov, 1, 0.1, 100);
  camera.position.z = config.cameraZ;

  // 场景在线性光里渲染，合成直接写显示空间 —— 同一种颜色存两份。
  const backgroundLinear = new THREE.Color(palette.bg);
  const backgroundSRGB = hexToSRGB(palette.bg);
  scene.background = backgroundLinear;

  const post = createPostPipeline(renderer, config, backgroundSRGB);
  const scroll = createScrollController(canvas, config);
  const trail = createTrail(renderer, canvas, config);

  const geometry = new THREE.PlaneGeometry(
    config.cardWidth, config.cardHeight, 48, 24,
  );

  // ---- 书脊网格（池） -------------------------------------------------------
  const cards = [];
  for (let m = 0; m < pool; m++) {
    const material = new THREE.ShaderMaterial({
      vertexShader: cardVertex,
      fragmentShader: cardFragment,
      uniforms: {
        uMap: { value: null },
        uImageRatio: { value: new THREE.Vector2(1, 1) },
        uBackground: { value: backgroundLinear },
        uBackfaceFade: { value: config.backfaceFade },
        uFogNear: { value: config.fogNear },
        uFogFar: { value: config.fogFar },
        uFogStrength: { value: config.fogStrength },
        uProgress: { value: 0 },
        // uIndex 是池里的固定位置，不是书的下标 —— 书是流动的
        uIndex: { value: m },
        uCount: { value: pool },
        uRadius: { value: config.radius },
        uPitch: { value: config.pitch },
        uAngleStep: { value: config.angleStep },
        uCurve: { value: config.curve },
        uShingle: { value: config.shingle },
        uVelocity: { value: 0 },
        uBend: { value: config.bend },
        uBendVertical: { value: 1 },
        uBendHorizontal: { value: 0 },
        uHover: { value: 0 },
        uDim: { value: 0 },
        uDimFade: { value: config.dimFade },
        uEntry: { value: 1 },
        uEntryScale: { value: config.entryScale },
        uEntrySoftness: { value: config.entrySoftness },
        uEntryAspect: { value: entryAspect() },
      },
      // 剔背面会让 90° 以后的书全消失；反正雾和 backfaceFade 会把它们
      // 洗成淡影，镜像纹理无所谓
      side: THREE.DoubleSide,
      // alpha 在这条管线里是深度不是透明度
      blending: THREE.NoBlending,
    });

    const mesh = new THREE.Mesh(geometry, material);
    mesh.userData.hoverRaw = { hover: 0, dim: 0 };
    mesh.userData.entry = 1;
    mesh.userData.entryOrder = m;
    mesh.userData.book = -1;
    // 位置全在 vertex shader 里算，CPU 侧包围球没有意义
    mesh.frustumCulled = false;
    scene.add(mesh);
    cards.push(mesh);
  }

  const cardBuffer = createCardBuffer(renderer, scene, cards, config);
  post.compositeMaterial.uniforms.uCardBuffer.value = cardBuffer.texture;

  // ---- 书脊装帧与纹理池 -----------------------------------------------------
  let bindery = createBindery(palette);
  const textureCache = new Map();
  const TEXTURE_CACHE_MAX = 96;

  function textureFor(bookIndex) {
    const idx = mod(bookIndex, count);
    let texture = textureCache.get(idx);
    if (!texture) {
      if (textureCache.size >= TEXTURE_CACHE_MAX) {
        const oldest = textureCache.keys().next().value;
        textureCache.get(oldest)?.dispose();
        textureCache.delete(oldest);
      }
      texture = bindery.spine(books[idx], idx);
      textureCache.set(idx, texture);
    }
    return texture;
  }

  /**
   * 池里第 m 个网格此刻该显示哪本书。
   *
   * 窗口里展示的书恒为 `[progress, progress + pool)`（mod 总数），
   * 网格 m 占的余数是 m —— 它的书只在滑出窗口的那一刻才换人，
   * 所以换纹理的频率是「每滑过一本换一张」，不是每帧全场换。
   */
  function bookForPoolSlot(m, progress) {
    return mod(m + pool * Math.floor((progress - m) / pool + 1), count);
  }

  function assignTextures(progress) {
    for (const [m, card] of cards.entries()) {
      const book = bookForPoolSlot(m, progress);
      if (book === card.userData.book) continue;
      card.userData.book = book;
      const texture = textureFor(book);
      card.material.uniforms.uMap.value = texture;
      const image = texture.image;
      const cardAspect = config.cardWidth / config.cardHeight;
      const imageAspect = image.width / image.height;
      card.material.uniforms.uImageRatio.value.set(
        Math.min(1, cardAspect / imageAspect),
        Math.min(1, imageAspect / cardAspect),
      );
    }
  }

  /** 换主题：背景、调色盘、书脊全部重画。 */
  function applyTheme(palette) {
    backgroundLinear.set(palette.bg);
    backgroundSRGB.copy(hexToSRGB(palette.bg));
    scene.background = backgroundLinear;
    for (const card of cards) {
      card.material.uniforms.uBackground.value = backgroundLinear;
    }
    const u = post.compositeMaterial.uniforms;
    u.uBackground.value = backgroundSRGB;
    // 抖动坡跟原版同款：黑—白—黑的色带，中间调提亮成白。
    // 主题色住在墙的底色和书脊上，颗粒保持原版的黑白。
    u.uInk.value.copy(hexToSRGB(palette.ink));
    u.uAccent.value.copy(hexToSRGB(palette.ditherAccent));
    u.uPaper.value.copy(hexToSRGB(palette.ink));
    u.uHoverInk.value.copy(hexToSRGB(palette.ink));
    u.uHoverAccent.value.copy(hexToSRGB(palette.ditherAccent));
    u.uHoverPaper.value.copy(hexToSRGB(palette.ink));
    u.uEntryInk.value.copy(hexToSRGB(palette.ink));
    u.uEntryAccent.value.copy(hexToSRGB(palette.ditherAccent));
    u.uEntryPaper.value.copy(hexToSRGB(palette.ink));
    u.uTrailInk.value.copy(hexToSRGB(palette.trailInk));
    u.uTrailAccent.value.copy(hexToSRGB(palette.ditherAccent));
    u.uTrailPaper.value.copy(hexToSRGB(palette.ink));
    // 书脊是烤死的颜色，换主题 = 换一套装帧重画
    bindery = createBindery(palette);
    for (const [, texture] of textureCache) texture.dispose();
    textureCache.clear();
    assignTextures(scroll.state.current);
  }

  // ---- entry ----------------------------------------------------------------
  // 原版等最后一张图落地才开场；我们的纹理是同步画的，直接开。
  let entryStart = performance.now();

  function parkForEntry() {
    scroll.state.current = -config.entrySpin;
    scroll.state.target = scroll.state.current;
    // 每次进场洗一次顺序 —— 固定顺序看两遍就成了「按剧本重播」
    const order = cards.map((_, index) => index);
    for (let i = order.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [order[i], order[j]] = [order[j], order[i]];
    }
    cards.forEach((card, index) => {
      card.userData.entryOrder = order[index];
      card.userData.entry = 1;
      card.material.uniforms.uEntry.value = 1;
    });
  }

  parkForEntry();
  assignTextures(scroll.state.current);

  // ---- 指针 -------------------------------------------------------------------
  const pointer = { x: 0, y: 0, inside: false };
  let hovered = -1;

  let travel = 0;
  let pointerSpeed = 0;
  let lastClient = null;

  // 点中的书脊会滑到正中，从光标底下溜走 —— 锁住它直到光标真的动了
  let locked = -1;
  const lockOrigin = { x: 0, y: 0 };

  const releaseLock = () => {
    locked = -1;
  };

  const onPointerMove = (event) => {
    const rect = canvas.getBoundingClientRect();
    pointer.x = (event.clientX - rect.left) / rect.width;
    pointer.y = 1 - (event.clientY - rect.top) / rect.height;
    pointer.inside =
      pointer.x >= 0 && pointer.x <= 1 && pointer.y >= 0 && pointer.y <= 1;

    if (lastClient) {
      travel += Math.hypot(event.clientX - lastClient.x, event.clientY - lastClient.y);
    } else {
      lastClient = { x: 0, y: 0 };
    }
    lastClient.x = event.clientX;
    lastClient.y = event.clientY;

    if (locked >= 0) {
      const travelled = Math.hypot(event.clientX - lockOrigin.x, event.clientY - lockOrigin.y);
      if (travelled > config.clickSlop) releaseLock();
    }
  };
  const onPointerLeave = () => {
    pointer.inside = false;
  };

  /** 把第 index 本书转到聚焦位（窗口中央）。 */
  function focusBook(index) {
    const base = mod(index, count) - pool / 2;
    const nearest = base + Math.round((scroll.state.current - base) / pool) * pool;
    scroll.goTo(nearest);
  }

  const press = { x: 0, y: 0 };

  const onPointerDown = (event) => {
    press.x = event.clientX;
    press.y = event.clientY;
    pointerSpeed = 0;
    travel = 0;
  };

  const onPointerUp = (event) => {
    if (hovered < 0) return;
    const travelled = Math.hypot(event.clientX - press.x, event.clientY - press.y);
    if (travelled > config.clickSlop) return;

    const book = cards[hovered]?.userData.book;
    if (book == null || book < 0) return;
    locked = hovered;
    lockOrigin.x = event.clientX;
    lockOrigin.y = event.clientY;
    focusBook(book);
    onOpen?.(mod(book, count));
  };

  canvas.addEventListener("pointermove", onPointerMove);
  canvas.addEventListener("pointerleave", onPointerLeave);
  canvas.addEventListener("pointerdown", onPointerDown);
  canvas.addEventListener("pointerup", onPointerUp);
  canvas.addEventListener("wheel", releaseLock, { passive: true });

  function resize() {
    const width = canvas.clientWidth || window.innerWidth;
    const height = canvas.clientHeight || window.innerHeight;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    post.setSize(width, height, renderer.getPixelRatio());
    cardBuffer.setSize(width, height);
    trail.setSize(width, height, renderer.getPixelRatio());
  }

  let frame = 0;
  let activeBook = -1;

  function tick() {
    frame = requestAnimationFrame(tick);

    config.autoSpin && (scroll.state.target += config.autoSpin);
    const progress = scroll.update();

    // 正中的书只在一变化时上报，别每帧摸 DOM
    const centred = mod(Math.round(progress) + Math.round(pool / 2), count);
    if (centred !== activeBook) {
      activeBook = centred;
      onCenterChange?.(centred);
    }

    pointerSpeed += (travel - pointerSpeed) * 0.35;
    travel = 0;

    const settled = !config.hoverIntent || pointerSpeed <= config.hoverSettleSpeed;

    cardBuffer.render(camera);
    const picked =
      locked < 0 && pointer.inside ? cardBuffer.pick(pointer.x, pointer.y) : -1;
    hovered = locked >= 0 ? locked : settled ? picked : hovered;
    canvas.style.cursor = hovered >= 0 ? "pointer" : "default";

    const anyHovered = hovered >= 0;
    const slotOf = (m) => {
      const slot = (m - progress) % pool;
      return slot < 0 ? slot + pool : slot;
    };
    const hoveredSlot = anyHovered ? slotOf(hovered) : 0;

    assignTextures(progress);

    const entryElapsed = performance.now() - entryStart;

    for (const [m, card] of cards.entries()) {
      const u = card.material.uniforms;
      const isHovered = m === hovered;
      const raw = card.userData.hoverRaw;

      const slot = slotOf(m);
      const separation = anyHovered ? Math.abs(slot - hoveredSlot) : 0;
      const dimTarget = anyHovered ? smoothstep(0, config.focusFalloff, separation) : 0;

      raw.hover = approach(raw.hover, isHovered ? 1 : 0, config);
      raw.dim = approach(raw.dim, dimTarget, config);

      const local = Math.min(
        1,
        Math.max(
          0,
          (entryElapsed - card.userData.entryOrder * config.entryStagger) /
            Math.max(1, config.entryDuration),
        ),
      );
      card.userData.entry = Math.min(
        card.userData.entry,
        1 - smoothstep(0, 1, Math.pow(local, config.entryCurve)),
      );

      u.uHover.value = Math.pow(raw.hover, config.hoverCurve);
      u.uDim.value = Math.pow(raw.dim, config.hoverCurve);
      u.uDimFade.value = config.dimFade;
      u.uEntry.value = card.userData.entry;
      u.uEntryScale.value = config.entryScale;
      u.uEntrySoftness.value = config.entrySoftness;
      u.uEntryAspect.value = entryAspect();
      u.uProgress.value = progress;
      u.uRadius.value = config.radius;
      u.uPitch.value = config.pitch;
      u.uAngleStep.value = config.angleStep;
      u.uCurve.value = config.curve;
      u.uShingle.value = config.shingle;
      u.uVelocity.value = scroll.state.bendVelocity;
      u.uBend.value = config.bend;
      u.uBendVertical.value = BEND_WEIGHTS[config.bendMode][0];
      u.uBendHorizontal.value = BEND_WEIGHTS[config.bendMode][1];
      u.uBackfaceFade.value = config.backfaceFade;
      u.uFogNear.value = config.fogNear;
      u.uFogFar.value = config.fogFar;
      u.uFogStrength.value = config.fogStrength;
    }

    const c = post.compositeMaterial.uniforms;
    c.uFocusSize.value = config.focusSize;
    c.uEdgePower.value = config.edgePower;
    c.uBlurStrength.value = config.blurStrength;
    c.uDitherScale.value = config.ditherScale;
    c.uMaxLevels.value = config.maxLevels;
    c.uMinLevels.value = config.minLevels;
    c.uFadeStrength.value = config.fadeStrength;
    c.uDitherAmount.value = config.dither ? config.ditherAmount : 0;
    c.uDitherStart.value = config.ditherStart;
    c.uDitherPower.value = config.ditherPower;
    c.uDitherDepth.value = config.ditherDepth;
    c.uGamma.value = config.ditherGamma;
    c.uMono.value = config.ditherMono;
    c.uDissolve.value = config.ditherDissolve;
    c.uHoverBlur.value = config.hoverBlur;
    c.uHoverBlurCurve.value = config.hoverBlurCurve;
    c.uHoverDither.value = config.hoverDither;
    c.uHoverDitherCurve.value = config.hoverDitherCurve;
    c.uHoverDitherLevels.value = config.hoverDitherLevels;
    c.uHoverDitherScale.value = config.hoverDitherScale;
    c.uHoverDitherCutoff.value = config.hoverDitherCutoff;
    c.uHoverGamma.value = config.hoverDitherGamma;
    c.uHoverMono.value = config.hoverDitherMono;
    c.uTrailAmount.value = config.trail ? config.trailAmount : 0;
    c.uTrailCutoff.value = config.trailCutoff;
    c.uTrailWarp.value = config.trailWarp;
    c.uTrailAberration.value = config.trailAberration;
    c.uTrailContrast.value = config.trailContrast;
    c.uTrailScale.value = config.trailScale;
    c.uTrailLevels.value = config.trailLevels;
    c.uTrailDissolve.value = config.trailDissolve;
    c.uTrailGamma.value = config.trailGamma;
    c.uTrailMono.value = config.trailMono;
    c.uTrailRim.value = config.trailRim;
    c.uTrailRimThickness.value = config.trailRimThickness;
    c.uTrailRimSoftness.value = config.trailRimSoftness;
    c.uEntryDither.value = config.entry ? config.entryDither : 0;
    c.uEntryScale.value = config.entryScale;
    c.uEntryLevels.value = config.entryDitherLevels;
    c.uEntryDissolve.value = config.entryDitherDissolve;
    c.uEntryGamma.value = config.entryDitherGamma;
    c.uEntryMono.value = config.entryDitherMono;
    c.uHoverClean.value = config.hoverClean;
    c.uCoupling.value = config.coupling;
    c.uStageStreakEnd.value = config.stageStreakEnd;
    c.uStageDitherBegin.value = config.stageDitherBegin;
    c.uStageHandoff.value = config.stageHandoff;
    c.uLift.value = config.lift;
    c.uDepthBlur.value = config.depthBlur;

    c.uTrail.value = trail.update();

    post.render(scene, camera);
  }

  const onResize = () => resize();
  window.addEventListener("resize", onResize);

  resize();
  tick();

  return {
    applyTheme,
    focusBook,
    /** 详情面板要用的大封面 */
    cover: (bookIndex) => bindery.cover(books[mod(bookIndex, count)]),
    dispose() {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", onResize);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerleave", onPointerLeave);
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("wheel", releaseLock);
      scroll.dispose();
      post.dispose();
      cardBuffer.dispose();
      trail.dispose();
      geometry.dispose();
      for (const [, texture] of textureCache) texture.dispose();
      textureCache.clear();
      cards.forEach((card) => {
        card.material.dispose();
      });
      renderer.dispose();
    },
  };
}
