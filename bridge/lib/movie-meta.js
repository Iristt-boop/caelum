/**
 * 电影元信息 —— 给票根填那几栏（片名/原名/年份/导演/类型/海报）。
 *
 * ## 🔴 为什么不是豆瓣
 *
 * 糖糖原话是「去豆瓣拉」。2026-09-04 从东京 VPS 实测三种入口，**全废**：
 *
 *   subject_suggest API   200 但永远返回 `[]` —— 静默风控，不给 403
 *   搜索页                200 能搜到，但明文里没有封面没有年份，
 *                         数据在加密的 `window.__DATA__` 里
 *   条目页                302 跳 sec.douban.com 验证码
 *
 * 解密 `__DATA__` 是个会一直变的活靶子，接了迟早坏，而且坏的时候
 * 大概率是**静默的**（又返回一个空结果），跟今天那个 404 的坑一模一样。
 *
 * ## ✅ 中文维基
 *
 * 拿她给的参考片实测，三部全中，有**真海报**：
 *
 *   阿甘正传    Forrestgumppost.jpg           「1994年的美國喜剧電影…」
 *   星际穿越    Interstellar_film_poster.jpg  「2014年…克里斯托弗·諾蘭執導」
 *   龙猫        My_Neighbor_Totoro…           「宫崎骏執導，1988年4月16日首映」
 *
 * 0.48s，无风控，不要 key 不要注册。REST API 是稳定契约，不是刮页面。
 *
 * ## ⚠️ 拉不到不是错误
 *
 * 冷门片、片名打错、维基没收录 —— 都很正常。这时候**返回她填的那点东西**，
 * 海报位留空，票根照样生成。
 *
 * 绝不因为查不到元信息就不给她票根 —— 那张票根记的是**你们看过这场**，
 * 元信息只是锦上添花。
 */

//: 繁 → 简。只覆盖类型/国家这几个词会用到的字，不做通用转换。
const CN_MAP = { 劇: "剧", 動: "动", 畫: "画", 懸: "悬", 愛: "爱", 險: "险",
  戰: "战", 爭: "争", 紀: "纪", 錄: "录", 樂: "乐", 傳: "传", 記: "记",
  國: "国", 灣: "湾", 韓: "韩" };
const normalizeCn = (s) => String(s).replace(/[劇動畫懸愛險戰爭紀錄樂傳記國灣韓]/g, (c) => CN_MAP[c] || c);

/** 摘要里能捞出来的那几栏。捞不到就是空字符串，不编。 */
function parseExtract(extract) {
  const s = String(extract || "");
  const out = { year: "", director: "", genres: [], country: "", released: "" };
  if (!s) return out;

  //: 年份：取第一个 19xx/20xx。摘要开头几乎都是「是一部 XXXX 年…」
  const y = s.match(/((?:19|20)\d{2})\s*年/);
  if (y) out.year = y[1];

  //: 上映日：「1988年4月16日首映」这种完整日期，有就用，比只有年份好
  const d = s.match(/((?:19|20)\d{2})年(\d{1,2})月(\d{1,2})日/);
  if (d) out.released = `${d[1]}-${String(d[2]).padStart(2, "0")}-${String(d[3]).padStart(2, "0")}`;

  //: 导演：「由XXX執導」/「XXX导演」。**只取名字，不含「執導」两个字**
  //: ⚠️ 摘要里常写「由罗伯·雷纳所执导」——「所」是助词不是名字的一部分。
  //: 2026-09-04 第一版没剥，票根上印的是「羅伯·雷納所」。
  //: 同理剥掉开头的连接词（「和」「与」「及」）。
  const dir = s.match(/由([^，。、）]{2,14}?)(?:所)?(?:執導|执导|導演|导演)/)
    || s.match(/([^，。、（）]{2,14}?)(?:所)?(?:執導|执导)/);
  if (dir) out.director = dir[1].replace(/^[和與与及、]+/, "").replace(/[所的]$/, "").trim();

  //: 类型：在摘要里出现就算。**不硬猜** —— 宁可少几个也不要给错的
  const GENRES = ["喜剧", "喜劇", "剧情", "劇情", "科幻", "動畫", "动画", "奇幻",
    "悬疑", "懸疑", "恐怖", "爱情", "愛情", "动作", "動作", "冒险", "冒險",
    "犯罪", "战争", "戰爭", "纪录", "紀錄", "音乐", "音樂", "传记", "傳記"];
  const seen = new Set();
  for (const g of GENRES) {
    if (s.includes(g)) {
      //: 繁简归一，别让「喜剧/喜劇」「动画/動畫」同时出现。
      //: ⚠️ 逐字替换，别漏 —— 2026-09-04 第一版漏了「畫」，
      //: 龙猫那张票根上就写着「动畫」，半繁半简
      const norm = normalizeCn(g);
      if (!seen.has(norm)) { seen.add(norm); out.genres.push(norm); }
    }
  }
  out.genres = out.genres.slice(0, 3);

  const c = s.match(/(美國|美国|中國|中国|日本|英國|英国|法國|法国|韓國|韩国|德國|德国|澳大利亞|澳大利亚|香港|台灣|台湾|印度|加拿大)/);
  if (c) out.country = normalizeCn(c[1]).replace(/亞/g, "亚");

  return out;
}

//: 维基的 `titles.display` 带 HTML 标签，票根上要的是纯文本
const stripTags = (s) => String(s || "").replace(/<[^>]*>/g, "").trim();

//: 再剥掉消歧义后缀：`怦然心动 (电影)` → `怦然心动`
const cleanTitle = (s) => stripTags(s)
  .replace(/\s*[（(][^）)]*[）)]\s*$/, "").trim();

/**
 * 查一部电影。**永远 resolve**，查不到就返回只有 title 的对象。
 *
 * @param {string} title 她填的片名
 * @param {(u: string, o?: object) => Promise<Response>} [fetchImpl] 测试注入
 */
const UA = "CaelumBridge/1.0 (https://noxtang.com; personal use)";
const HEAD = { "User-Agent": UA, Accept: "application/json" };

/** 直接按标题拿摘要。没有/不是条目就返回 null。 */
async function summary(name, fetchImpl) {
  try {
    const r = await fetchImpl(
      `https://zh.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(name)}`,
      { headers: HEAD },
    );
    if (!r.ok) return null;                       //: 404 = 没收录
    const d = await r.json();
    //: 消歧义页不算命中 —— 拿它的摘要会得到一堆无关的东西
    return d?.type === "disambiguation" ? null : d;
  } catch {
    return null;                                  //: 网络不通 / 超时
  }
}

/**
 * 直查不中时，用搜索接口找出正确的条目名。
 *
 * 为什么需要：很多片名在维基上带消歧义后缀。2026-09-04 实测
 * 「怦然心动」直查 404，而搜索能找到「怦然心动 (电影)」。
 *
 * ⚠️ 搜索词后面缀「电影」，否则「怦然心动」会先命中
 * 《怦然心动的人生整理魔法》那种同名前缀的书。
 */
async function findTitle(name, fetchImpl) {
  try {
    const r = await fetchImpl(
      "https://zh.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=3"
      + `&srsearch=${encodeURIComponent(name + " 电影")}`,
      { headers: HEAD },
    );
    if (!r.ok) return "";
    const hits = (await r.json())?.query?.search || [];
    //: 🔴 **只认两种形状**：完全同名，或者 `片名 (消歧义)`。
    //:
    //: ⚠️ 第一版写的是 `startsWith(name)` —— 那条规则**根本不区分**：
    //: 「怦然心动的人生整理魔法」也是以「怦然心动」开头的，
    //: 于是搜片名会拿到一本讲整理的书。测试当场抓到了。
    //:
    //: 宁可查不到（票根照样出，只是没元信息），
    //: 也不要把一部无关的作品印在她的票根上。
    //: 不用正则 —— 片名里可能有 `(` `.` 这些元字符，转义一层套一层
    //: 容易写错（写这行时就错过一次）。字符串判断更笨也更稳。
    const ok = (t) => {
      if (t === name) return true;
      if (!t.startsWith(name)) return false;
      const rest = t.slice(name.length).trimStart();
      return rest.startsWith("(") || rest.startsWith("（");
    };
    const hit = hits.map((h) => String(h.title || "")).find(ok);
    return hit || "";
  } catch {
    return "";
  }
}

export async function lookupMovie(title, fetchImpl = fetch) {
  const name = String(title || "").trim();
  const empty = {
    title: name, original: "", year: "", director: "",
    genres: [], country: "", released: "", poster: "", source: "",
  };
  if (!name) return empty;

  let data = await summary(name, fetchImpl);
  if (!data) {
    //: 直查不中 → 搜一次拿正确条目名 → 再查一次。**最多两跳**
    const better = await findTitle(name, fetchImpl);
    if (better) data = await summary(better, fetchImpl);
  }
  if (!data) return empty;

  const parsed = parseExtract(data?.extract);
  return {
    title: name,
    //: 维基的标题可能和她填的不一样（繁简、别名），两个都留着
    //: ⚠️ `titles.display` 是**带 HTML 的**（`<span lang="zh">…</span>`），
    //: 直接用会把标签印在票根上。取纯文本，去完标签如果和她填的一样就不留
    //: ⚠️ 维基条目名常带消歧义后缀（`怦然心动 (电影)`），那不是「原名」，
    //: 印在票根上很怪。剥掉括号里那段再比 —— 剥完一样就不留
    original: cleanTitle(data?.titles?.display) !== name
      ? cleanTitle(data?.titles?.display) : "",
    ...parsed,
    poster: data?.thumbnail?.source || data?.originalimage?.source || "",
    source: data?.content_urls?.desktop?.page || "",
  };
}

export const _parseExtract = parseExtract;   //: 只给测试用


/**
 * 这场够不够格问她「这是什么」。
 *
 * 点开两分钟就关的不算一场 —— 为那种场次弹窗纯属打扰。
 * 门槛同 `shared_activities.py` 里共影那条 ≥10 分钟的线。
 *
 * ⚠️ 抽成函数是为了**能单测**：从接口那头测的话，
 * 测试造出来的场次开始和结束只差几毫秒，永远过不了门槛
 * （2026-09-04 就是这么卡住的）。
 */
export function longEnough(startedAt, endedAt, minMinutes = 10) {
  const a = new Date(startedAt).getTime();
  const b = new Date(endedAt).getTime();
  if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
  return (b - a) / 60000 >= minMinutes;
}
