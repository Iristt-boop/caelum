// bridge 的日期算法自检。bridge 没有测试套件，这几个函数又全是日期数学 ——
// 跨周、跨年、周日边界最容易错，而错了是静默的（他就是不来追你）。
// 跑法：node scratch/check_todo_time.js

function cnNowAt(date, hm) {
  return { date, hm, weekday: new Date(`${date}T00:00:00Z`).getUTCDay() || 7 };
}
function weekStart(dateStr, weekday) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - (weekday - 1));
  return d.toISOString().slice(0, 10);
}
function doneThisWeek(t, now) {
  const start = weekStart(now.date, now.weekday);
  return String(t.done_log || "").split(",").map(s => s.trim())
    .filter(d => d && d >= start && d <= now.date).length;
}
function doneToday(t, now) {
  return String(t.done_log || "").split(",").map(s => s.trim()).includes(now.date);
}
function hitsToday(t, now) {
  const rep = t.repeat || (t.at ? "once" : "anytime");
  if (rep === "anytime") return false;
  if (rep === "daily") return !doneToday(t, now);
  if (rep === "weekly") {
    return String(t.weekdays || "").split(",").map(s => s.trim()).includes(String(now.weekday))
      && !doneToday(t, now);
  }
  if (rep === "weekly_count") return doneThisWeek(t, now) < (t.times || 1);
  return (t.due || String(t.created_at || "").slice(0, 10)) === now.date;
}

let bad = 0;
const ok = (name, got, want) => {
  const pass = JSON.stringify(got) === JSON.stringify(want);
  if (!pass) { bad++; console.log(`✗ ${name}: 得到 ${JSON.stringify(got)}，应该是 ${JSON.stringify(want)}`); }
  else console.log(`✓ ${name}`);
};

// 2026-08-18 是周二
ok("周几算对", cnNowAt("2026-08-18", "20:00").weekday, 2);
ok("周日算成 7", cnNowAt("2026-08-23", "20:00").weekday, 7);
ok("周一是本周起点", weekStart("2026-08-18", 2), "2026-08-17");
ok("周日的本周起点还是那个周一", weekStart("2026-08-23", 7), "2026-08-17");
ok("跨月不出错", weekStart("2026-09-01", 2), "2026-08-31");
ok("跨年不出错", weekStart("2027-01-01", 5), "2026-12-28");

const tue = cnNowAt("2026-08-18", "20:00");

// 糖糖给的三个例子
ok("背单词 每天 18:00 —— 今天没做就命中",
  hitsToday({ repeat: "daily", at: "18:00" }, tue), true);
ok("背单词 今天做过了就不再命中",
  hitsToday({ repeat: "daily", at: "18:00", done_log: "2026-08-18" }, tue), false);
ok("背单词 昨天做过不影响今天",
  hitsToday({ repeat: "daily", at: "18:00", done_log: "2026-08-17" }, tue), true);

ok("attention source 8/18 9:00 单次 —— 今天命中",
  hitsToday({ repeat: "once", at: "09:00", due: "2026-08-18" }, tue), true);
ok("单次 不是今天就不命中",
  hitsToday({ repeat: "once", at: "09:00", due: "2026-08-19" }, tue), false);

const gym = { repeat: "weekly_count", times: 3, at: "20:00" };
ok("健身 每周3次 —— 一次没做，命中", hitsToday(gym, tue), true);
ok("健身 本周做了2次，还命中",
  hitsToday({ ...gym, done_log: "2026-08-17,2026-08-18" }, tue), true);
ok("健身 本周做满3次，不再命中",
  hitsToday({ ...gym, done_log: "2026-08-17,2026-08-18,2026-08-18" }, tue), false);
ok("健身 上周做的3次不算进本周",
  hitsToday({ ...gym, done_log: "2026-08-10,2026-08-11,2026-08-12" }, tue), true);

ok("每周二四 —— 今天周二，命中",
  hitsToday({ repeat: "weekly", weekdays: "2,4", at: "19:00" }, tue), true);
ok("每周一四 —— 今天周二，不命中",
  hitsToday({ repeat: "weekly", weekdays: "1,4", at: "19:00" }, tue), false);

ok("随时档不占提醒名额", hitsToday({ repeat: "anytime" }, tue), false);

console.log(bad === 0 ? "\n全过。" : `\n${bad} 个不对。`);
process.exit(bad === 0 ? 0 : 1);
