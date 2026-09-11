// 全部可调参数集中在这。移植自 dunyaochen/-3D-（MIT，原作 Yousuf Soomro）。
//
// 数值不是拿仓库默认值抄的 —— 是按作者 README 实拍图里 GUI 面板显示的
// 调校值（半径 3.5 / 相机 8.8 / 视角 59 / 层叠 0.145 / 螺距 0.94）对齐的，
// 再把「横版卡片」的密度比例换算到「书脊」的长宽比上：
//   角度步长 = 0.845 × 卡宽 / 半径（原版弧长/卡宽 ≈ 0.845，层层搭着）
//   螺距     = 0.482 × 卡高（原版螺距/卡高 ≈ 0.482，竖向叠一半）
// 效果对齐的目标只有一条：跟仓库 README 那张图一个样。

export const config = {
  // 池化窗口 —— 不管藏书多少，屏幕上同时只存在这么多张书脊。
  // 数据 500+ 本时靠 scene.js 里的槽位循环换纹理，这就是「虚拟滚动」。
  pool: 21,

  // Helix
  radius: 3.5,
  pitch: 1.01,
  angleStep: 0.188,
  curve: 1.0,
  cardWidth: 0.78,
  cardHeight: 2.1,
  // 相邻书脊半径逐槽错开，叠着的书才有前后层次（原版 0.145）
  shingle: 0.145,
  backfaceFade: 0,

  // Atmosphere —— 远处的书沉进雾里
  fogNear: 8.0,
  fogFar: 20.6,
  fogStrength: 1.0,
  depthBlur: 1.0,
  lift: 0,

  // Camera（原版实拍值）
  cameraZ: 8.8,
  fov: 59,

  // Scroll
  wheelStrength: 0.0022,
  dragStrength: 0.007,
  ease: 0.075,
  autoSpin: 0.0,

  // Snap —— 惯性耗尽后停在最近一本书上（单本滑动）
  snap: true,
  snapSpeed: 0.02,
  snapDelay: 300,
  snapStiffness: 0.04,
  snapDamping: 0.54,

  // Entry —— 进场：每本书脊从自己的中心以抖动圆盘长出来。
  // 参考仓库等图片异步加载完才开场；我们的书脊是 canvas 同步画的，
  // 所以直接开。旋转步数按角度步长换算，保持约 1.2 圈的登场弧线。
  entry: true,
  entryDuration: 1050,
  entryStagger: 150,
  entryCurve: 1.0,
  entrySoftness: 0.39,
  entryScale: 9.5,
  entryRound: 1,

  entrySpin: 49,
  entrySpinDuration: 2400,
  entryEaseIn: 0.29,
  entryEaseOut: 0.94,

  entryDither: 0.45,
  entryDitherLevels: 4,
  entryDitherDissolve: 1.0,
  // 这几色运行时由 applyTheme 写入（跟随主题），这里只是初始占位
  entryDitherInk: "#000000",
  entryDitherAccent: "#ffffff",
  entryDitherPaper: "#000000",
  entryDitherGamma: 1.5,
  entryDitherMono: 0.25,

  // Hover —— 指到哪本，哪本清晰，其余退后
  hoverInEase: 0.095,
  hoverOutEase: 0.07,
  hoverCurve: 0.95,
  dimFade: 0.67,
  hoverClean: 1.0,
  hoverIntent: true,
  hoverSettleSpeed: 8,
  focusFalloff: 0.7,

  hoverBlur: 0.13,
  hoverBlurCurve: 1.0,

  hoverDither: 0.3,
  hoverDitherCurve: 1.9,
  hoverDitherLevels: 8,
  hoverDitherScale: 10,
  hoverDitherCutoff: 0.22,
  hoverDitherInk: "#000000",
  hoverDitherAccent: "#ffffff",
  hoverDitherPaper: "#000000",
  hoverDitherGamma: 1.8,
  hoverDitherMono: 0.22,

  clickToFocus: true,
  clickSlop: 6,
  focusDuration: 1300,
  focusEaseIn: 0.35,
  focusEaseOut: 0.98,

  cardBufferScale: 0.5,

  // Motion bend —— 转动时书脊像有韧性一样弯一下
  bend: 2.7,
  bendMode: "horizontal",
  bendEase: 0.12,
  bendMaxVelocity: 0.07,

  // Edge treatment
  focusSize: 0.25,
  edgePower: 1.65,
  blurStrength: 0.47,
  streakAngle: 90,
  streakSpread: 4.5,
  streakAnisotropy: 0,

  // Dissolve staging
  coupling: 0.55,
  stageStreakEnd: 0.55,
  stageDitherBegin: 0.45,
  stageHandoff: 0.75,

  // Frame-edge dither（原版默认值，一颗都不动）
  dither: true,
  ditherAmount: 0.77,
  ditherStart: 0.64,
  ditherPower: 1.25,
  ditherDepth: 1.0,
  ditherScale: 7.5,
  maxLevels: 8,
  minLevels: 8,
  fadeStrength: 0.4,

  ditherInk: "#000000",
  ditherAccent: "#ffffff",
  ditherPaper: "#000000",
  ditherGamma: 1.8,
  ditherMono: 0.22,
  ditherDissolve: 0,

  // Cursor trail
  trail: true,
  trailRadius: 132,
  trailSpeedInfluence: 1,
  trailSpeedRange: 6,
  trailDecay: 0.962,
  trailDissipate: 1.6,
  trailSmoothing: 0.24,
  trailIdleDelay: 220,
  trailIdleDecay: 0.869,
  trailIdleDrift: false,

  trailAmount: 0.78,
  trailCutoff: 0.125,
  trailWarp: 0.36,
  trailAberration: 0,
  trailContrast: 0.77,
  trailScale: 8.5,
  trailLevels: 6,
  trailDissolve: 1.0,
  trailInk: "#1a1a1a",
  trailAccent: "#ffffff",
  trailPaper: "#000000",
  trailGamma: 2.0,
  trailMono: 0.29,
  trailRim: 0,
  trailRimColor: "#ffffff",
  trailRimThickness: 0.3,
  trailRimSoftness: 0.45,

  // 运行时由 applyTheme 写入（主题色的近黑，视觉上就是原版的黑幕）
  background: "#000000",
};
