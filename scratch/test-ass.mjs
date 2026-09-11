// parseAss 单测（写盘避开 shell 转义污染）。node scratch/test-ass.mjs
import { parseAss, parseSubtitleText } from "../nox-app/caelum-os-ui/src/lib/localVideo.js";

const ass = [
  "[Script Info]",
  "Title: t",
  "",
  "[Events]",
  "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
  "Dialogue: 0,0:00:01.20,0:00:03.50,Default,,0,0,0,,奥斯华在暗杀后被审问12小时",
  "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,{\\i1}for 12 hours{\\i0} after the, assassination",
  "Dialogue: 0,0:00:07.00,0:00:08.00,Default,,0,0,0,,第一行\\N第二行",
  "Dialogue: 0,0:00:09.00,0:00:10.00,OP,,0,0,0,,{\\pos(192,30)}特效歌词",
].join("\n");

const items = parseAss(ass);
const texts = items.map((i) => i.text);
const checks = [
  ["条数=4", items.length === 4],
  ["台词内逗号保留", texts[1] === "for 12 hours after the, assassination"],
  ["覆盖标签剥离", texts[3] === "特效歌词"],
  ["软换行\\N→空格", texts[2] === "第一行 第二行"],
  ["起点秒数", Math.abs(items[0].start - 1.2) < 0.01],
  ["自动分发按头识别", parseSubtitleText(ass).length === 4],
];
let fail = 0;
for (const [name, ok] of checks) {
  console.log((ok ? "PASS" : "FAIL"), name, ok ? "" : "→ " + JSON.stringify(texts));
  if (!ok) fail++;
}
process.exit(fail ? 1 : 0);
