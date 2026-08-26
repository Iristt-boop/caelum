import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  annotatePassage,
  collectCard,
  continueReading,
  deleteBook,
  dismissCard,
  getProgress,
  listCardInbox,
  listCardCollection,
  listCards,
  listAnnotations,
  listBooks,
  listChunks,
  markRead,
  readCard,
  readChunk,
  replyToAnnotation,
  searchChunks,
  submitUserNotes,
} from "./store.js";
import { importBook } from "./importer.js";
import { renderCardPng, renderCardSvg } from "./card-renderer.js";
import { MAX_REPLIES_PER_SUBMIT, replyToNote } from "./nox-client.js";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const publicDir = path.join(ROOT, "public");
const defaultMaxBodyBytes = Number(process.env.READING_HTTP_MAX_BODY_BYTES || process.env.READING_IMPORT_MAX_BYTES || 25_000_000);

const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
};

export function sendJson(res, status, value) {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(value, null, 2));
}

export function sendError(res, status, message) {
  sendJson(res, status, { error: message });
}

export async function readBody(req, { maxBytes = defaultMaxBodyBytes, allowEmpty = true } = {}) {
  const contentType = req.headers["content-type"] || "";
  if (contentType && !contentType.includes("application/json")) {
    const err = new Error("Content-Type must be application/json");
    err.statusCode = 415;
    throw err;
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > maxBytes) throw new Error(`Request body exceeds ${maxBytes} bytes`);
    chunks.push(chunk);
  }
  if (!chunks.length) {
    if (allowEmpty) return {};
    throw new Error("Missing JSON body");
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function routeParts(url) {
  return url.pathname
    .split("/")
    .filter(Boolean)
    .map((part) => decodeURIComponent(part));
}

export async function handleApi(req, res, url, options = {}) {
  const parts = routeParts(url);
  const maxBytes = options.maxBodyBytes || defaultMaxBodyBytes;

  if (req.method === "GET" && parts.length === 2 && parts[1] === "books") {
    return sendJson(res, 200, await listBooks({ includePrivate: true }));
  }

  if (req.method === "GET" && parts.length === 4 && parts[1] === "books" && parts[3] === "chunks") {
    return sendJson(res, 200, await listChunks(parts[2], { includePrivate: true }));
  }

  if (req.method === "DELETE" && parts.length === 3 && parts[1] === "books") {
    return sendJson(res, 200, await deleteBook(parts[2]));
  }

  if (req.method === "GET" && parts.length === 5 && parts[1] === "books" && parts[3] === "chunks") {
    return sendJson(res, 200, await readChunk(parts[2], parts[4]));
  }

  if (req.method === "GET" && parts.length === 2 && parts[1] === "continue") {
    return sendJson(res, 200, await continueReading({ bookId: url.searchParams.get("bookId") || undefined }));
  }

  if (req.method === "GET" && parts.length === 2 && parts[1] === "annotations") {
    return sendJson(
      res,
      200,
      await listAnnotations({
        bookId: url.searchParams.get("bookId") || undefined,
        chunkId: url.searchParams.get("chunkId") || undefined,
        kind: url.searchParams.get("kind") || undefined,
        author: url.searchParams.get("author") || undefined,
        status: url.searchParams.get("status") || undefined,
        parentId: url.searchParams.has("parentId") ? url.searchParams.get("parentId") : undefined,
        includePrivate: true,
      }),
    );
  }

  if (req.method === "GET" && parts.length === 2 && parts[1] === "cards") {
    return sendJson(
      res,
      200,
      await listCards({
        bookId: url.searchParams.get("bookId") || undefined,
        chunkId: url.searchParams.get("chunkId") || undefined,
        source: url.searchParams.get("source") || undefined,
        limit: Number(url.searchParams.get("limit") || 20),
        offset: Number(url.searchParams.get("offset") || 0),
      }),
    );
  }

  if (req.method === "GET" && parts.length === 2 && parts[1] === "card-collection") {
    return sendJson(
      res,
      200,
      await listCardCollection({
        bookId: url.searchParams.get("bookId") || undefined,
        limit: Number(url.searchParams.get("limit") || 12),
        offset: Number(url.searchParams.get("offset") || 0),
      }),
    );
  }

  if (req.method === "GET" && parts.length === 2 && parts[1] === "card-inbox") {
    return sendJson(
      res,
      200,
      await listCardInbox({
        bookId: url.searchParams.get("bookId") || undefined,
        limit: Number(url.searchParams.get("limit") || 10),
      }),
    );
  }

  if (req.method === "GET" && parts.length === 4 && parts[1] === "cards" && parts[3] === "image.svg") {
    const card = await readCard(parts[2]);
    res.writeHead(200, { "content-type": "image/svg+xml; charset=utf-8" });
    res.end(renderCardSvg(card));
    return;
  }

  if (req.method === "GET" && parts.length === 4 && parts[1] === "cards" && parts[3] === "image.png") {
    const card = await readCard(parts[2]);
    res.writeHead(200, { "content-type": "image/png" });
    res.end(renderCardPng(card));
    return;
  }

  if (req.method === "POST" && parts.length === 4 && parts[1] === "cards" && parts[3] === "dismiss") {
    return sendJson(res, 200, await dismissCard(parts[2]));
  }

  if (req.method === "POST" && parts.length === 2 && parts[1] === "cards") {
    const body = await readBody(req, { maxBytes });
    return sendJson(res, 201, await collectCard({ ...body, createdBy: body.createdBy || "human" }));
  }

  if (req.method === "POST" && parts.length === 2 && parts[1] === "annotations") {
    const body = await readBody(req, { maxBytes });
    return sendJson(
      res,
      201,
      await annotatePassage({
        ...body,
        author: body.author || "user",
        status: body.status || "open",
      }),
    );
  }

  if (req.method === "POST" && parts.length === 2 && parts[1] === "replies") {
    const body = await readBody(req, { maxBytes });
    return sendJson(
      res,
      201,
      await replyToAnnotation({
        ...body,
        author: body.author || "user",
        kind: body.kind || "reply",
        status: body.status || "open",
      }),
    );
  }

  if (req.method === "POST" && parts.length === 2 && parts[1] === "submit-notes") {
    const result = await submitUserNotes(await readBody(req, { maxBytes }));
    // 提交已经落库了，下面生成回应失败也不能回滚 —— 那是两件事
    return sendJson(res, 200, { ...result, ...(await autoReply(result)) });
  }

  if (req.method === "POST" && parts.length === 2 && parts[1] === "mark-read") {
    const body = await readBody(req, { maxBytes });
    return sendJson(res, 200, await markRead(body.bookId, body.chunkId));
  }

  if (req.method === "POST" && parts.length === 2 && parts[1] === "import") {
    return sendJson(res, 201, await importBook(await readBody(req, { maxBytes })));
  }

  if (req.method === "GET" && parts.length === 2 && parts[1] === "progress") {
    return sendJson(res, 200, await getProgress(url.searchParams.get("bookId") || undefined));
  }

  if (req.method === "GET" && parts.length === 2 && parts[1] === "search") {
    return sendJson(
      res,
      200,
      await searchChunks({
        bookId: url.searchParams.get("bookId") || undefined,
        query: url.searchParams.get("q") || "",
        limit: Number(url.searchParams.get("limit") || 10),
      }),
    );
  }

  return sendError(res, 404, "Not found");
}

/**
 * 提交批注之后，让 Nox 逐条回到批注下面。
 *
 * 这是「推模式」的那根线：以前糖糖点完发送，还得自己去 chat 说一句
 * 「我批注了」，他才会调 reading_list_notes 去看。现在发送即回应。
 *
 * 三条边界：
 *
 * 1. **回应失败不影响提交。** 批注状态已经落库了，模型没回上来是另一回事。
 *    返回体里带 `replyError` 告诉前端，但整个请求仍然是 200。
 * 2. **逐条回，各走各的会话。** `session_id = reading-<批注id>`，
 *    所以每条批注是一条独立对话线，也不会污染主 chat 的上下文。
 * 3. **有上限。** 一次最多回 MAX_REPLIES_PER_SUBMIT 条 —— 她要是连划一整章，
 *    逐条调模型既慢又贵，剩下的照常提交、只是他不逐条接话。
 */
async function autoReply(result) {
  const notes = (result?.notes || []).filter((n) => n?.id && !n.parentId);
  if (!notes.length) return {};

  const targets = notes.slice(0, MAX_REPLIES_PER_SUBMIT);

  // 章节正文直接读，不从 submission context 里取。
  // 那份 context 是 chunk-once-per-session 的：同一章第二次提交就只剩
  // omittedChunks，正文不在里面 —— 靠它的话，她在同一章的第二条批注
  // 就会失去前后文。readChunk 自己带缓存（store.js 的 chunkTextCache），
  // 重复读几乎没成本。
  const chunkCache = new Map();
  const loadChunk = async (bookId, chunkId) => {
    const key = `${bookId}/${chunkId}`;
    if (!chunkCache.has(key)) {
      try {
        chunkCache.set(key, await readChunk(bookId, chunkId));
      } catch {
        chunkCache.set(key, null);   // 读不到就没有前后文，不致命
      }
    }
    return chunkCache.get(key);
  };

  const replied = [];
  const failed = [];
  for (const note of targets) {
    const chunk = await loadChunk(note.bookId, note.chunkId);
    try {
      const text = await replyToNote({
        note,
        chunkText: chunk?.text || "",
        bookTitle: chunk?.title || "",
        chunkTitle: chunk?.chunk?.title || "",
      });
      // 不传 status —— 非人类作者默认就是 published（store.js:1247）。
      // 传 "open" 会让它看起来像一条「待糖糖发送的私人笔记」。
      const saved = await replyToAnnotation({
        parentId: note.id,
        note: text,
        author: "nox",
        kind: "reply",
      });
      replied.push({ parentId: note.id, replyId: saved?.id || null });
    } catch (error) {
      // 单条失败不拖累其余的
      failed.push({ parentId: note.id, error: error.message || String(error) });
    }
  }

  const out = { replied, repliedCount: replied.length };
  if (failed.length) {
    out.replyError = failed[0].error;
    out.replyFailed = failed;
  }
  if (notes.length > targets.length) {
    out.replySkipped = notes.length - targets.length;
  }
  return out;
}

export async function serveStatic(req, res, url) {
  const requested = url.pathname === "/" ? "reader.html" : url.pathname.slice(1);
  const resolved = path.resolve(publicDir, requested);
  const relative = path.relative(publicDir, resolved);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    return sendError(res, 403, "Forbidden");
  }

  try {
    const body = await readFile(resolved);
    res.writeHead(200, {
      "content-type": contentTypes[path.extname(resolved)] || "application/octet-stream",
    });
    res.end(body);
  } catch (error) {
    if (error.code === "ENOENT") return sendError(res, 404, "Not found");
    throw error;
  }
}
