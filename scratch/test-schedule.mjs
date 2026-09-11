// schedule.js 的断言自测。node scratch/test-schedule.mjs 直接跑，不打网络。
// 全过打 PASS，任何一条不过就 throw。
import {
  occursOn,
  scheduleFor,
  shiftDate,
  isoWeekday,
  todayKeyCN,
  nowHMCN,
} from "../nox-app/frontend/src/lib/schedule.js";

let passed = 0;
const ok = (cond, name) => {
  if (!cond) throw new Error("FAIL: " + name);
  passed++;
};

// ---- 基准：2026-08-27 是周四 ----
ok(isoWeekday("2026-08-27") === 4, "周四=4");
ok(todayKeyCN(new Date("2026-08-27T18:00:00+08:00")) === "2026-08-27", "todayKeyCN 格式");
ok(nowHMCN(new Date("2026-08-27T18:07:00+08:00")) === "18:07", "nowHMCN");
ok(shiftDate("2026-09-01", -1) === "2026-08-31", "跨月回退");
ok(shiftDate("2026-08-27", 1) === "2026-08-28", "普通+1");

// ---- occursOn 各模式 ----
const weekly = { repeat: "weekly", weekdays: "2,4" };
ok(occursOn(weekly, "2026-08-27"), "每周二四命中周四");
ok(!occursOn(weekly, "2026-08-28"), "每周二四不命中周五");

const once = { repeat: "once", due: "2026-09-01" };
ok(occursOn(once, "2026-09-01"), "once 命中 due");
ok(!occursOn(once, "2026-08-31"), "once 不命中别的天");
const legacyOnce = { repeat: "once", due: "", createdAt: "2026-08-20T03:00:00.000Z" };
ok(occursOn(legacyOnce, "2026-08-20"), "老 once 无 due 回落创建日");

const daily = { repeat: "daily" };
ok(occursOn(daily, "2026-08-27") && occursOn(daily, "2026-12-25"), "daily 天天命中");

ok(!occursOn({ repeat: "anytime" }, "2026-08-27"), "anytime 不落日");
ok(!occursOn({ repeat: "weekly_count", times: 3 }, "2026-08-27"), "配额不落日");

// ---- scheduleFor 分组与排序 ----
const items = [
  { id: "1", text: "拉伸", repeat: "daily", at: "21:30" },
  { id: "2", text: "晨报", repeat: "daily", at: "09:00" },
  { id: "3", text: "随手记", repeat: "anytime" },
  { id: "4", text: "健身", repeat: "weekly_count", times: 3 },
  { id: "5", text: "订花", repeat: "once", due: "2026-09-01", at: "10:00" },
];
const todayS = scheduleFor(items, "2026-08-27");
ok(todayS.isToday === true, "今天识别");
ok(todayS.timed.map((i) => i.text).join() === "晨报,拉伸", "带时刻升序（订花不在今天）");
ok(todayS.anytime.length === 1 && todayS.quota.length === 1, "随手和配额只挂今天");

const otherDay = scheduleFor(items, "2026-09-01");
ok(otherDay.timed.map((i) => i.text).join() === "晨报,订花,拉伸", "9月1日=daily两条+当天的订花，按时刻排");
ok(otherDay.anytime.length === 0 && otherDay.quota.length === 0 && !otherDay.isToday, "预览日不带库存");

console.log(`PASS ${passed} assertions`);
