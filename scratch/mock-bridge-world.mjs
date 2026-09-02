// 假 bridge，只供 World 页看效果。
// ⚠️ 不许拿糖糖的正式环境试（never-test-in-tangtang-prod），而且线上
// 现在压根还没有 /api/nox/world —— 接口还没部署。
//
// 用 ?s= 切场景：
//   sunny  晴天白天 · 她在家 · 主卧灯+空调开着 · 客厅电视在放
//   rain   雨天     · 她不在家（整屋压暗）· 只剩电热毯
//   night  夜里     · 她在家 · 在打游戏（电竞房那格亮）
//   broken 天气挂了 + HA 挂了 + 够不到她电脑（三段都该说读不到）
import http from "node:http";

const PORT = 3999;
const now = () => new Date().toISOString();

/** 几分钟前的 HH:MM。夜晚场景靠它把日落挪到「此刻之前」。 */
const hhmmAgo = (mins) => {
  const d = new Date(Date.now() - mins * 60000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

const dev = (name, entity_id, state, attrs = {}) => {
  const [room, ...rest] = name.includes(" ") ? name.split(" ") : [null, name];
  return {
    name, label: rest.join(" ") || name,
    room: name === "蒸蛋器" ? "厨房" : room,
    entity_id, domain: entity_id.split(".")[0],
    ok: true, state, changed_at: now(), attrs,
  };
};

const SCENES = {
  sunny: {
    sky: { ok: true, text: "晴", icon: "100", temp: 28, feels_like: 30, humidity: 55,
           temp_max: 31, temp_min: 22, sunrise: "06:05", sunset: "18:50", has_forecast: true },
    presence: { ok: true, home: true, state: "home", since: now() },
    activity: { ok: true, app: null, seconds: 0 },
    devices: [
      dev("主卧 床头灯", "light.yeelink", "on", { brightness: 180 }),
      dev("主卧 空调", "climate.lumi_d2c3", "cool", { temperature: 25 }),
      dev("主卧 风扇", "fan.dmaker", "off"),
      dev("客厅 电视", "media_player.xiaomi", "playing", { media_title: "底特律：变人" }),
      dev("客厅 空调", "climate.lumi_d7b8", "off"),
      dev("电竞房 空调", "climate.gua_shi", "off"),
      dev("蒸蛋器", "switch.cuco", "off"),
    ],
  },
  rain: {
    sky: { ok: true, text: "中雨", icon: "306", temp: 19, humidity: 92,
           temp_max: 21, temp_min: 17, sunrise: "06:05", sunset: "18:50", has_forecast: true },
    presence: { ok: true, home: false, state: "not_home", since: now() },
    activity: { ok: false, error: "够不到她的电脑（分不出是网关没起、网不通，还是电脑关着）" },
    devices: [
      dev("主卧 电热毯", "switch.xiaomi_mj1", "on"),
      dev("主卧 床头灯", "light.yeelink", "off"),
      dev("客厅 空调", "climate.lumi_d7b8", "off"),
      { ...dev("电竞房 空调", "climate.gua_shi", "unavailable"), ok: true },
    ],
  },
  night: {
    // ⚠️ 日出日落要相对**此刻**算，不能写死。页面判白天/黑夜用的是真实时间，
    // 写死 18:50 的话下午看这个场景只会看到白天的天色 —— 场景等于没生效。
    sky: { ok: true, text: "多云", icon: "101", temp: 24,
           temp_max: 29, temp_min: 21,
           sunrise: hhmmAgo(11 * 60), sunset: hhmmAgo(70), has_forecast: true },
    presence: { ok: true, home: true, state: "home", since: now() },
    activity: { ok: true, app: "Delta Force", seconds: 3600 },
    devices: [
      dev("电竞房 空调", "climate.gua_shi", "cool", { temperature: 24, current_temperature: 27 }),
      dev("主卧 床头灯", "light.yeelink", "off"),
      dev("客厅 电视", "media_player.xiaomi", "off"),
    ],
  },
  broken: {
    sky: { ok: false, error: "ConnectionError" },
    presence: { ok: false, error: "HA 说 unknown" },
    activity: { ok: false, error: "够不到她的电脑（分不出是网关没起、网不通，还是电脑关着）" },
    devices: null,
  },
};

http.createServer((req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "*");
  const u = new URL(req.url, "http://x");
  if (req.method === "OPTIONS") return res.writeHead(204).end();

  if (u.pathname === "/api/nox/world") {
    const s = SCENES[u.searchParams.get("s") || process.env.SCENE || "sunny"] || SCENES.sunny;
    res.writeHead(200, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({
      ok: true, now: now(),
      sky: s.sky,
      home: s.devices ? { ok: true, devices: s.devices }
                      : { ok: false, error: "ConnectionError", devices: [] },
      presence: s.presence,
      activity: s.activity,
    }));
  }

  res.writeHead(200, { "Content-Type": "application/json" });
  res.end("{}");
}).listen(PORT, () => console.log(`[mock-world] http://localhost:${PORT}  场景：?s=sunny|rain|night|broken`));
