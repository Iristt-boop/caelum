#!/usr/bin/env node
/**
 * 把 uploads 里没有 gallery 记录的图片补进相册。
 *
 * 起因：2026-07-28 盘点发现磁盘 10 个文件、数据库只有 4 条记录 ——
 * 六张照片躺在磁盘上但相册里看不见。根因是只有 /api/images/upload
 * 会写 gallery，别的落盘路径（语音条、日记配图等）只写文件。
 *
 * 不是全都补：那次盘点里有三张 64x64、1KB 的图，是测试或缩略图残留，
 * 塞进相册只会碍眼。所以按尺寸和体积过滤。
 *
 * 用法：
 *   node scripts/backfill_gallery.js          # 只看会补哪些，不动数据
 *   node scripts/backfill_gallery.js --apply  # 真的写入
 */

// bridge 的 package.json 是 "type": "module"，所以走 import 不是 require
import crypto from "crypto";
import fs from "fs";
import path from "path";
import initSqlJs from "sql.js";

const UPLOAD_DIR = process.env.UPLOAD_DIR || "/root/data/uploads";
const DB_PATH = process.env.DB_PATH || "/root/data/nox-bridge.db";
const APPLY = process.argv.includes("--apply");

// 过滤门槛。低于这个的多半不是糖糖的照片，是图标 / 测试图 / 缩略图残留。
const MIN_BYTES = 10 * 1024;
const MIN_EDGE = 200;

/** 不装库读图片尺寸，只认 JPEG 和 PNG。 */
function dimensions(file) {
  const fd = fs.openSync(file, "r");
  try {
    const head = Buffer.alloc(32);
    fs.readSync(fd, head, 0, 32, 0);

    if (head.slice(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) {
      return { w: head.readUInt32BE(16), h: head.readUInt32BE(20) };
    }
    if (head[0] === 0xff && head[1] === 0xd8) {
      const size = fs.fstatSync(fd).size;
      const buf = Buffer.alloc(Math.min(size, 512 * 1024));
      fs.readSync(fd, buf, 0, buf.length, 0);
      let i = 2;
      while (i < buf.length - 9) {
        if (buf[i] !== 0xff) { i++; continue; }
        const marker = buf[i + 1];
        // SOF0..SOF15，跳过 DHT(C4) / JPG(C8) / DAC(CC)
        if (marker >= 0xc0 && marker <= 0xcf &&
            marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
          return { h: buf.readUInt16BE(i + 5), w: buf.readUInt16BE(i + 7) };
        }
        i += 2 + buf.readUInt16BE(i + 2);
      }
    }
  } catch {
    /* 读不出来就当未知 */
  } finally {
    fs.closeSync(fd);
  }
  return { w: 0, h: 0 };
}

(async () => {
  const SQL = await initSqlJs();
  const db = new SQL.Database(fs.readFileSync(DB_PATH));

  const recorded = new Set();
  const stmt = db.prepare("SELECT url FROM gallery");
  while (stmt.step()) recorded.add(path.basename(stmt.getAsObject().url));
  stmt.free();

  const files = fs.readdirSync(UPLOAD_DIR)
    .map((n) => path.join(UPLOAD_DIR, n))
    .filter((p) => fs.statSync(p).isFile());

  const willAdd = [];
  const skipped = [];

  for (const file of files) {
    const name = path.basename(file);
    if (recorded.has(name)) continue;

    const size = fs.statSync(file).size;
    const { w, h } = dimensions(file);
    const tooSmall = size < MIN_BYTES || (w && Math.max(w, h) < MIN_EDGE);

    (tooSmall ? skipped : willAdd).push({ name, size, w, h });
  }

  console.log(`磁盘 ${files.length} 个 / 已入库 ${recorded.size} 个\n`);

  if (skipped.length) {
    console.log("跳过（太小，多半是图标或测试图）：");
    for (const s of skipped) {
      console.log(`  ${s.name}  ${(s.size / 1024).toFixed(0)}KB  ${s.w}x${s.h}`);
    }
    console.log();
  }

  if (!willAdd.length) {
    console.log("没有需要补录的图片。");
    return;
  }

  console.log(`${APPLY ? "补录" : "将补录"} ${willAdd.length} 张：`);
  for (const a of willAdd) {
    console.log(`  ${a.name}  ${(a.size / 1024).toFixed(0)}KB  ${a.w}x${a.h}`);
  }

  if (!APPLY) {
    console.log("\n（这是预演。加 --apply 才会真的写入）");
    return;
  }

  for (const a of willAdd) {
    const url = `/uploads/${a.name}`;
    // created_at 用文件的修改时间，不用现在 —— 否则老照片会全部冒到相册最前面
    const mtime = fs.statSync(path.join(UPLOAD_DIR, a.name)).mtime.toISOString();
    db.run("INSERT INTO gallery VALUES (?,?,?,?,?,?)",
           [crypto.randomUUID(), url, url, 0, "补录", mtime]);
  }
  fs.writeFileSync(DB_PATH, Buffer.from(db.export()));
  console.log(`\n已写入 ${willAdd.length} 条，相册=「补录」`);
})();
