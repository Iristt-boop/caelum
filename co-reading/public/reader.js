// ?v= 是给部署用的：静态文件服务不发 Cache-Control / ETag，
// 不带版本号改了浏览器可能还吃旧的。改 card-logic.js 时把日期一起改。
import { sharedNoteIdSet } from "./card-logic.js?v=20260804";

const state = {
  books: [],
  chunks: [],
  annotations: [],
  bookId: null,
  chunkId: null,
  chunk: null,
  quote: "",
  quoteOffset: null,
  selectedQuote: "",
  selectedQuoteOffset: null,
  activeAnnotationId: null,
  toastTimer: null,
  refreshInFlight: false,
  composing: false,
  replyDrafts: {},
};

// 浮标角标：新卡/新收藏的计数，打开抽屉就清零。
let badgeCount = 0;
function bumpBadge(n = 1) {
  badgeCount += n;
  const el = $("fold-badge");
  el.textContent = String(badgeCount);
  el.hidden = false;
}
function resetBadge() {
  badgeCount = 0;
  $("fold-badge").hidden = true;
}

const $ = (id) => document.getElementById(id);
const authTokenKey = "co-reading-auth-token";
const urlToken = new URLSearchParams(location.search).get("token");
if (urlToken) {
  localStorage.setItem(authTokenKey, urlToken);
  history.replaceState(null, "", location.pathname);
}

async function api(path, options = {}) {
  const token = localStorage.getItem(authTokenKey);
  const response = await fetch(path, {
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error || new Error("Could not read file"));
    reader.onload = () => {
      const bytes = new Uint8Array(reader.result);
      let binary = "";
      const size = 0x8000;
      for (let index = 0; index < bytes.length; index += size) {
        binary += String.fromCharCode(...bytes.subarray(index, index + size));
      }
      resolve(btoa(binary));
    };
    reader.readAsArrayBuffer(file);
  });
}

function isMobileLayout() {
  return window.matchMedia("(max-width: 980px)").matches;
}

function scrollToPanel(selector) {
  if (!isMobileLayout()) return;
  requestAnimationFrame(() => {
    document.querySelector(selector)?.scrollIntoView({ block: "start", behavior: "smooth" });
  });
}

function showToast(message) {
  clearTimeout(state.toastTimer);
  $("toast").textContent = message;
  $("toast").hidden = false;
  state.toastTimer = setTimeout(() => {
    $("toast").hidden = true;
  }, 2400);
}

function formatIdentity(author) {
  const value = String(author || "unknown").toLowerCase();
  if (value === "user" || value === "koshi") return "you";
  if (value === "claude") return "Claude";
  return value;
}

// 朋友圈式评论区里的名字：只有两个人，不用完整身份
function nameZh(author) {
  const value = String(author || "").toLowerCase();
  return value === "user" || value === "koshi" ? "你" : "他";
}

function repliesFor(parentId, notes) {
  return notes.filter((item) => item.parentId === parentId);
}

function replyCount(parentId, notes, seen = new Set()) {
  if (seen.has(parentId)) return 0;
  seen.add(parentId);
  return repliesFor(parentId, notes).reduce((count, reply) => count + 1 + replyCount(reply.id, notes, seen), 0);
}

function flattenReplies(parentId, notes, seen = new Set()) {
  const out = [];
  for (const reply of repliesFor(parentId, notes)) {
    if (!reply.id || seen.has(reply.id)) continue;
    seen.add(reply.id);
    out.push(reply);
    out.push(...flattenReplies(reply.id, notes, seen));
  }
  return out;
}

// 朋友圈评论区：一行一条「他：…」，没有卡片没有抬头
function renderReply(reply) {
  return `<p class="comment"><span class="c-name">${escapeHtml(nameZh(reply.author))}：</span>${escapeHtml(reply.note)}</p>`;
}

function renderThread(note, notes) {
  const replies = flattenReplies(note.id, notes);
  const draft = state.replyDrafts[note.id] || "";
  if ((note.kind || "note") === "collect" && !replies.length) {
    // 只收藏、还没递给他：评论区里先放一个「递给他」
    return `<div class="comments">
      <button type="button" class="reply-ask" data-collect-ask="${escapeHtml(note.id)}">递给他 · 让他在下面回一句</button>
    </div>`;
  }
  return `<div class="comments">
    ${replies.map((reply) => renderReply(reply)).join("")}
    <form class="reply-form" data-parent-id="${escapeHtml(note.id)}">
      <textarea rows="1" placeholder="再问一句…">${escapeHtml(draft)}</textarea>
      <button type="submit" class="reply-send">说</button>
    </form>
  </div>`;
}

function renderInlineNote(note, notes) {
  return `<aside class="inline-note" data-note-id="${escapeHtml(note.id)}">
    <p class="inline-note-kicker">${escapeHtml(formatIdentity(note.author))} · ${escapeHtml(note.kind || "note")}</p>
    <p class="note-body">${escapeHtml(note.note)}</p>
    ${renderThread(note, notes)}
  </aside>`;
}

function renderBooks() {
  $("books").innerHTML = state.books
    .map((book) => {
      const total = book.chunkCount || 0;
      const read = book.chunksRead || 0;
      const pct = total ? Math.round((read / total) * 100) : 0;
      return `<div class="book-row ${book.bookId === state.bookId ? "active" : ""}">
        <button class="book" data-book="${escapeHtml(book.bookId)}">
          <span class="book-title">${escapeHtml(book.title || book.bookId)}</span>
          <span class="book-meta">${escapeHtml(book.author || "未知作者")} · ${read}/${total} · ${book.annotationCount || 0} 条笔记</span>
          <span class="progress"><span style="width: ${pct}%"></span></span>
        </button>
        <button class="book-delete" data-delete-book="${escapeHtml(book.bookId)}" title="删除这本书">删除</button>
      </div>`;
    })
    .join("");
}

function renderChunks() {
  $("chunks").innerHTML = state.chunks
    .map(
      (chunk) => `<button class="chunk ${chunk.id === state.chunkId ? "active" : ""}" data-chunk="${escapeHtml(chunk.id)}">
        <span class="chunk-title">${escapeHtml(chunk.title)}</span>
        <span class="chunk-meta">${escapeHtml(chunk.id)} · ${chunk.read ? "已读" : "未读"} · ${chunk.annotationCount || 0} 条笔记</span>
      </button>`,
    )
    .join("");
}

function renderText() {
  if (!state.chunk) return;
  const text = state.chunk.text || "";
  const notes = state.annotations.filter((item) => item.chunkId === state.chunkId);
  const sharedIds = sharedNoteIdSet(notes);
  const highlights = [];
  const occupied = [];
  const rootNotes = notes
    .filter((item) => !item.parentId && item.quote)
    .sort((a, b) => {
      const left = Number.isInteger(a.quoteOffset) ? a.quoteOffset : text.indexOf(a.quote);
      const right = Number.isInteger(b.quoteOffset) ? b.quoteOffset : text.indexOf(b.quote);
      return left - right;
    });
  for (const note of rootNotes) {
    const quote = String(note.quote || "");
    const requestedOffset = Number(note.quoteOffset);
    const start =
      Number.isInteger(requestedOffset) && requestedOffset >= 0 && text.slice(requestedOffset, requestedOffset + quote.length) === quote
        ? requestedOffset
        : text.indexOf(quote);
    if (!quote || start < 0) continue;
    const end = start + quote.length;
    if (occupied.some((range) => start < range.end && end > range.start)) continue;
    occupied.push({ start, end });
    highlights.push({ start, end, note, shared: sharedIds.has(note.id) });
  }

  let html = "";
  let cursor = 0;
  for (const highlight of highlights) {
    html += escapeHtml(text.slice(cursor, highlight.start));
    const quote = escapeHtml(text.slice(highlight.start, highlight.end));
    const bookmark = highlight.shared ? `<span class="shared-bookmark" title="这里有两个人的折痕。">此处有回声</span>` : "";
    // 收藏和批注在正文里的分量不一样：收藏是细虚线，批注是高亮
    const kindClass = highlight.note.kind === "collect" ? "collect-mark" : "";
    html += `<mark class="${highlight.note.id === state.activeAnnotationId ? "active" : ""} ${highlight.shared ? "shared" : ""} ${kindClass}" data-note-id="${escapeHtml(highlight.note.id)}" title="${escapeHtml(highlight.note.note || "")}">${quote}</mark>${bookmark}${
      highlight.note.id === state.activeAnnotationId ? renderInlineNote(highlight.note, notes) : ""
    }`;
    cursor = highlight.end;
  }
  html += escapeHtml(text.slice(cursor));
  $("text").innerHTML = html;
  bindMarkActions();
}

function bindMarkActions() {
  document.querySelectorAll("mark[data-note-id]").forEach((mark) => {
    const open = (event) => {
      event.stopPropagation();
      activateAnnotation(mark.dataset.noteId, { scroll: true });
    };
    mark.addEventListener("click", open);
    mark.addEventListener("touchend", open);
  });
}

function renderAnnotations() {
  const notes = state.annotations.filter((item) => item.chunkId === state.chunkId);
  const roots = notes.filter((item) => !item.parentId);
  // 收藏是收藏的轻，批注是批注的重 —— 分开渲染
  const collects = roots.filter((item) => (item.kind || "note") === "collect");
  const noteRoots = roots.filter((item) => (item.kind || "note") !== "collect");
  const openCount = state.annotations.filter((item) => item.author === "user" && (item.status || "open") === "open")
    .length;

  const collectHtml = collects
    .map((note) => {
      const expanded = note.id === state.activeAnnotationId;
      const replied = flattenReplies(note.id, notes).length > 0;
      return `<div class="collect-row ${expanded ? "active" : ""}" data-note-id="${escapeHtml(note.id)}" tabindex="0">
        <p class="collect-quote">「${escapeHtml(note.quote)}」</p>
        <p class="collect-meta">你 · 收藏 · ${escapeHtml(String(note.createdAt || "").slice(5, 10))}${replied ? " · 他回了" : ""}</p>
        ${expanded ? renderThread(note, notes) : ""}
      </div>`;
    })
    .join("");

  const noteHtml = noteRoots
    .map((note) => {
      const replies = replyCount(note.id, notes);
      const expanded = note.id === state.activeAnnotationId;
      const isShared = sharedNoteIdSet(notes).has(note.id);
      return `<article class="note-card ${(note.status || "") === "open" ? "open" : ""} ${expanded ? "active" : ""}" data-note-id="${escapeHtml(note.id)}" tabindex="0">
        ${isShared ? `<p class="shared-line">这里有两个人的折痕。</p>` : ""}
        <p class="note-quote">${escapeHtml(note.quote)}</p>
        <p class="note-body">${escapeHtml(note.note)}</p>
        <div class="note-meta">${escapeHtml(formatIdentity(note.author))} · ${escapeHtml(note.kind || "note")} · ${escapeHtml(note.status || "published")}${replies ? ` · ${replies} replies` : ""}</div>
        ${expanded ? renderThread(note, notes) : ""}
      </article>`;
    })
    .join("");

  $("margins").innerHTML = collectHtml + noteHtml;

  $("submit-notes").disabled = openCount === 0;
  $("submit-notes").textContent = openCount ? `发送 ${openCount} 条给 Claude` : "发送给 Claude";
  $("status").textContent = openCount
    ? `${openCount} 条私人笔记待发送。`
    : "私人笔记留在本地，直到你发送。";
}

function currentBook() {
  return state.books.find((item) => item.bookId === state.bookId) || {};
}

function currentChunkMeta() {
  return state.chunks.find((item) => item.id === state.chunkId) || state.chunk?.chunk || {};
}

/* ---- 折痕抽屉：收藏 / 卡册 / 前情 ---- */

function ritualCardHtml(card) {
  return `<div class="card-holder">
    ${renderReadingCard(card)}
    <div class="card-holder-actions">
      <a class="secondary-button" href="/api/cards/${encodeURIComponent(card.id)}/image.png" target="_blank" rel="noopener">存成 PNG</a>
    </div>
  </div>`;
}

function recapCardHtml(card) {
  return `<div class="recap-card">
    <p class="card-kicker">${escapeHtml(card.kicker || "前情 · 回访")}</p>
    <h3>${escapeHtml(card.title || "")}</h3>
    <p class="recap-body">${escapeHtml(card.note || card.quote || "")}</p>
    <p class="recap-foot">${escapeHtml(card.footer || "")} · <a href="/api/cards/${encodeURIComponent(card.id)}/image.png" target="_blank" rel="noopener">存成 PNG</a></p>
  </div>`;
}

async function loadDrawer() {
  if (!state.bookId) {
    $("collect-empty").hidden = false;
    $("collect-list").innerHTML = "";
    $("card-empty").hidden = false;
    $("card-list").innerHTML = "";
    $("recap-empty").hidden = false;
    $("recap-list").innerHTML = "";
    return;
  }

  // 收藏：整本书的，不只当前章
  const collects = state.annotations
    .filter((item) => (item.kind || "note") === "collect" && !item.parentId)
    .sort((a, b) => String(b.createdAt || "").localeCompare(String(a.createdAt || "")));
  $("collect-empty").hidden = collects.length > 0;
  $("collect-list").innerHTML = collects
    .map((note) => {
      const date = String(note.createdAt || "").slice(5, 10);
      const chunkTitle = state.chunks.find((chunk) => chunk.id === note.chunkId)?.title || note.chunkId;
      const replied = flattenReplies(note.id, state.annotations).length > 0;
      return `<div class="passage" data-jump-chunk="${escapeHtml(note.chunkId)}" data-jump-quote="${escapeHtml(note.quote)}">
        <p class="p-q">「${escapeHtml(note.quote)}」</p>
        <p class="p-m">${escapeHtml(chunkTitle)} · ${escapeHtml(date)}${replied ? " · 他回了" : ""} <button type="button" class="p-act" data-collect-ask="${escapeHtml(note.id)}">递给他</button></p>
      </div>`;
    })
    .join("");

  try {
    const cards = await api(`/api/cards?bookId=${encodeURIComponent(state.bookId)}&limit=50`);
    const recaps = cards.filter((card) => card.source === "recap");
    const ritualCards = cards.filter((card) => card.source !== "recap" && (card.status || "new") !== "dismissed");
    $("card-empty").hidden = ritualCards.length > 0;
    $("card-list").innerHTML = ritualCards.map(ritualCardHtml).join("");
    $("recap-empty").hidden = recaps.length > 0;
    $("recap-list").innerHTML = recaps.map(recapCardHtml).join("");
  } catch {
    // 卡册拉不下来不影响收藏 tab
  }
}

function switchDrawerTab(tabId) {
  const tabs = [
    ["tab-collects", "panel-collects"],
    ["tab-cards", "panel-cards"],
    ["tab-recap", "panel-recap"],
  ];
  for (const [tab, panel] of tabs) {
    $(tab).classList.toggle("active", tab === tabId);
    $(panel).hidden = tab !== tabId;
  }
}

// 从收藏跳回原文：开那一章，然后把那句滚到屏幕中间
function scrollToQuote(quote) {
  const el = $("text");
  if (!el || !quote) return;
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const parts = { text: "", nodes: [] };
  let node;
  while ((node = walker.nextNode())) {
    parts.text += node.nodeValue;
    parts.nodes.push(node);
  }
  const index = parts.text.indexOf(quote);
  if (index < 0) return;
  let acc = 0;
  let target = null;
  let offset = 0;
  for (const textNode of parts.nodes) {
    const len = textNode.nodeValue.length;
    if (index < acc + len) {
      target = textNode;
      offset = index - acc;
      break;
    }
    acc += len;
  }
  if (!target) return;
  const range = document.createRange();
  range.setStart(target, offset);
  range.setEnd(target, Math.min(offset + quote.length, target.nodeValue.length));
  target.parentElement?.scrollIntoView({ block: "center", behavior: "smooth" });
}

/* ---- 自动已读：翻章/读到底就算读过，卡从这里长出来 ---- */

async function autoMarkRead(chunkId) {
  if (!state.bookId || !chunkId) return;
  try {
    const result = await api("/api/mark-read", {
      method: "POST",
      body: { bookId: state.bookId, chunkId },
    });
    renderChunks();
    if (result.collectedBookCard) {
      bumpBadge(1);
      showToast("合卷卡落进卡册了（折痕 ✦）");
    } else if (result.collectedCard) {
      bumpBadge(1);
      showToast("✦ 收获一枚折痕卡（卡册里）");
    }
  } catch {
    // 自动已读失败不该打扰阅读 —— 下次翻章再试
  }
}

function seededRandom(seed) {
  let value = (Number(seed) || 1) >>> 0;
  return () => {
    value ^= value << 13;
    value ^= value >>> 17;
    value ^= value << 5;
    return (value >>> 0) / 4294967296;
  };
}

function readingCardArt(card) {
  const random = seededRandom(card.artSeed || 1);
  if (card.art === "ripple") {
    const centers = [
      [25 + random() * 18, 20 + random() * 18],
      [58 + random() * 18, 48 + random() * 18],
      [22 + random() * 14, 72 + random() * 12],
    ];
    const circles = centers
      .flatMap(([cx, cy], groupIndex) =>
        Array.from({ length: groupIndex === 1 ? 4 : 3 }, (_, index) => {
          const radius = 8 + index * (6 + random() * 3) + random() * 2;
          const opacity = 0.035 + random() * 0.06;
          return `<circle cx="${cx.toFixed(2)}" cy="${cy.toFixed(2)}" r="${radius.toFixed(2)}" opacity="${opacity.toFixed(3)}" />`;
        }),
      )
      .join("");
    return `<svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid slice" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="0.36">${circles}</g></svg>`;
  }
  if (card.art === "stardust") {
    const dots = Array.from({ length: 64 }, () => {
      const cx = 7 + random() * 86;
      const cy = 8 + random() * 80;
      const radius = 0.08 + random() * 0.24;
      const opacity = 0.18 + random() * 0.42;
      return `<circle cx="${cx.toFixed(2)}" cy="${cy.toFixed(2)}" r="${radius.toFixed(2)}" opacity="${opacity.toFixed(3)}" />`;
    }).join("");
    const bright = Array.from({ length: 7 }, () => {
      const cx = 12 + random() * 76;
      const cy = 12 + random() * 72;
      const opacity = 0.22 + random() * 0.26;
      return `<path d="M ${(cx - 0.9).toFixed(2)} ${cy.toFixed(2)} L ${(cx + 0.9).toFixed(2)} ${cy.toFixed(2)} M ${cx.toFixed(2)} ${(cy - 0.9).toFixed(2)} L ${cx.toFixed(2)} ${(cy + 0.9).toFixed(2)}" opacity="${opacity.toFixed(3)}" />`;
    }).join("");
    const lines = Array.from({ length: 5 }, () => {
      const x1 = 8 + random() * 84;
      const y1 = 10 + random() * 76;
      const x2 = x1 + (random() - 0.5) * 12;
      const y2 = y1 + (random() - 0.5) * 12;
      return `<path d="M ${x1.toFixed(2)} ${y1.toFixed(2)} L ${x2.toFixed(2)} ${y2.toFixed(2)}" opacity="0.07" />`;
    }).join("");
    return `<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><g fill="currentColor">${dots}</g><g fill="none" stroke="currentColor" stroke-width="0.14">${lines}${bright}</g></svg>`;
  }
  const lines = Array.from({ length: 14 }, () => {
    const x = 8 + random() * 84;
    const drift = (random() - 0.5) * 10;
    const opacity = 0.06 + random() * 0.14;
    return `<path d="M ${x.toFixed(2)} 3 C ${(x + drift).toFixed(2)} 30 ${(x - drift).toFixed(2)} 62 ${x.toFixed(2)} 97" opacity="${opacity.toFixed(3)}" />`;
  }).join("");
  return `<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="0.32">${lines}</g></svg>`;
}

function renderReadingCard(card) {
  return `<article class="ritual-card ${escapeHtml(card.variant)} art-${escapeHtml(card.art || "fold")} ${escapeHtml(cardSizeClass(card))}">
    <div class="card-art">${readingCardArt(card)}</div>
    <div class="card-content">
      <p class="card-kicker">${escapeHtml(card.kicker)}</p>
      <h3>${escapeHtml(card.title)}</h3>
      <p class="card-subtitle">${escapeHtml(card.subtitle)}</p>
      <blockquote>${escapeHtml(card.quote)}</blockquote>
      <div class="card-voices ${card.rightText ? "" : "single"}">
        <section>
          <span>${escapeHtml(card.leftLabel)}</span>
          <p>${escapeHtml(card.leftText)}</p>
        </section>
        ${
          card.rightText
            ? `<section>
                <span>${escapeHtml(card.rightLabel)}</span>
                <p>${escapeHtml(card.rightText)}</p>
              </section>`
            : ""
        }
      </div>
      <footer>${escapeHtml(card.footer)}</footer>
    </div>
  </article>`;
}

function cardSizeClass(card) {
  const totalLength = [card.quote, card.leftText, card.rightText, card.note]
    .filter(Boolean)
    .join("")
    .length;
  if (totalLength < 120) return "card-compact";
  if (totalLength > 360) return "card-tall";
  return "card-standard";
}

function updateSelectionAction() {
  const selection = window.getSelection();
  const details = selectionDetails(selection);
  state.selectedQuote = details?.quote || "";
  state.selectedQuoteOffset = details?.quoteOffset ?? null;
  const ready = Boolean(state.selectedQuote && state.bookId && state.chunkId);
  $("collect-selection").disabled = !ready;
  $("note-selection").disabled = !ready;
  $("ask-selection").disabled = !ready;
}

function elementForNode(node) {
  return node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
}

function countOccurrences(haystack, needle) {
  if (!needle) return 0;
  let count = 0;
  let index = 0;
  while (index <= haystack.length) {
    const found = haystack.indexOf(needle, index);
    if (found === -1) break;
    count += 1;
    index = found + Math.max(needle.length, 1);
  }
  return count;
}

function findOccurrence(haystack, needle, occurrence) {
  let index = -1;
  let from = 0;
  for (let current = 0; current <= occurrence; current += 1) {
    index = haystack.indexOf(needle, from);
    if (index === -1) return -1;
    from = index + Math.max(needle.length, 1);
  }
  return index;
}

function selectionDetails(selection) {
  if (!selection || selection.rangeCount === 0 || !state.chunk?.text) return null;
  const rawQuote = selection.toString();
  const quote = rawQuote.trim();
  if (!quote) return null;

  const range = selection.getRangeAt(0);
  const textEl = $("text");
  const startEl = elementForNode(range.startContainer);
  const endEl = elementForNode(range.endContainer);
  if (!startEl || !endEl) return null;
  if (!textEl.contains(range.commonAncestorContainer) || !textEl.contains(startEl) || !textEl.contains(endEl)) return null;
  if (startEl.closest(".inline-note, .shared-bookmark") || endEl.closest(".inline-note, .shared-bookmark")) return null;

  const prefixRange = range.cloneRange();
  prefixRange.selectNodeContents(textEl);
  prefixRange.setEnd(range.startContainer, range.startOffset);
  const occurrence = countOccurrences(prefixRange.toString(), quote);
  const quoteOffset = findOccurrence(state.chunk.text, quote, occurrence);
  return {
    quote,
    quoteOffset: quoteOffset >= 0 ? quoteOffset : null,
  };
}

async function loadBooks() {
  state.books = await api("/api/books");
  renderBooks();
}

async function selectBook(bookId) {
  state.bookId = bookId;
  state.chunkId = null;
  state.chunk = null;
  state.activeAnnotationId = null;
  state.replyDrafts = {};
  state.chunks = await api(`/api/books/${encodeURIComponent(bookId)}/chunks`);
  state.annotations = await api(`/api/annotations?bookId=${encodeURIComponent(bookId)}`);
  const book = state.books.find((item) => item.bookId === bookId);
  $("book-meta").textContent = book?.author || "未知作者";
  $("book-title").textContent = book?.title || bookId;
  $("chunk-file").textContent = "未选择章节";
  $("chunk-title").textContent = "打开一章开始阅读";
  $("text").innerHTML = `<p class="empty">选择一个章节。选中文字即可收藏或留言。</p>`;
  $("continue-reading").disabled = false;
  document.body.classList.add("has-book");
  document.body.classList.remove("has-chunk");
  renderBooks();
  renderChunks();
  renderAnnotations();
  maybeShowRecapBanner(book);
  scrollToPanel(".chapters");
}

function clearBookSelection() {
  state.bookId = null;
  state.chunkId = null;
  state.chunk = null;
  state.annotations = [];
  state.chunks = [];
  state.activeAnnotationId = null;
  state.replyDrafts = {};
  $("book-meta").textContent = "选择一本书";
  $("book-title").textContent = "书架";
  $("chunk-file").textContent = "未选择章节";
  $("chunk-title").textContent = "打开一章开始阅读";
  $("text").innerHTML = `<p class="empty">选择一本书和章节。选中文字即可收藏或留言。</p>`;
  $("continue-reading").disabled = true;
  $("recap-banner").hidden = true;
  document.body.classList.remove("has-book", "has-chunk");
  renderChunks();
  renderAnnotations();
}

async function deleteBookFromShelf(bookId) {
  const book = state.books.find((item) => item.bookId === bookId);
  const label = book?.title || bookId;
  if (!confirm(`从图书馆删除《${label}》？\n\n文件和相关笔记会归档到 data/trash。`)) return;

  const result = await api(`/api/books/${encodeURIComponent(bookId)}`, { method: "DELETE" });
  $("status").textContent = result.message || `已删除《${label}》。`;
  await loadBooks();
  if (state.bookId === bookId) clearBookSelection();
  renderBooks();
}

async function selectChunk(chunkId) {
  // 翻章自动算已读：上一章没标过，就静默标掉 —— 卡和前情的位置地基
  const previous = state.chunkId;
  if (previous && previous !== chunkId && state.bookId) {
    const prevEntry = state.chunks.find((chunk) => chunk.id === previous);
    if (prevEntry && !prevEntry.read) autoMarkRead(previous);
  }
  state.chunkId = chunkId;
  state.activeAnnotationId = null;
  state.chunk = await api(`/api/books/${encodeURIComponent(state.bookId)}/chunks/${encodeURIComponent(chunkId)}`);
  $("chunk-file").textContent = state.chunk.chunk.id;
  $("chunk-title").textContent = state.chunk.chunk.title;
  $("continue-reading").disabled = false;
  document.body.classList.add("has-chunk");
  renderChunks();
  renderText();
  renderAnnotations();
  scrollToPanel(".reader");
}

function openNoteForm(quote) {
  state.quote = quote.trim();
  state.quoteOffset = state.selectedQuote === state.quote ? state.selectedQuoteOffset : null;
  if (!state.bookId || !state.chunkId || !state.quote) return;
  $("quote-preview").textContent = state.quote;
  $("note").value = "";
  $("note-form").hidden = false;
  $("note").focus();
}

function activateAnnotation(noteId, { scroll = false } = {}) {
  state.activeAnnotationId = noteId;
  renderText();
  renderAnnotations();
  if (scroll) {
    document.querySelector(`.inline-note[data-note-id="${CSS.escape(noteId)}"], .note-card[data-note-id="${CSS.escape(noteId)}"]`)?.scrollIntoView({
      block: "nearest",
      behavior: "smooth",
    });
  }
}

function isEditingDraft() {
  const active = document.activeElement;
  return Boolean(
    state.composing ||
      active?.matches?.("textarea, input") ||
      active?.closest?.(".reply-form, .note-form"),
  );
}

async function refreshCurrent({ force = false } = {}) {
  if (state.refreshInFlight) return;
  if (!force && isEditingDraft()) return;
  state.refreshInFlight = true;
  try {
    await loadBooks();
    if (state.bookId) {
      if (!state.books.some((book) => book.bookId === state.bookId)) {
        clearBookSelection();
        $("status").textContent = "这本书已从图书馆删除。";
        return;
      }
      state.chunks = await api(`/api/books/${encodeURIComponent(state.bookId)}/chunks`);
      state.annotations = await api(`/api/annotations?bookId=${encodeURIComponent(state.bookId)}`);
      renderBooks();
      renderChunks();
      renderText();
      renderAnnotations();
      // 抽屉开着的话，数据也跟着活
      if (!$("drawer-wrap").hidden) await loadDrawer();
    }
  } finally {
    state.refreshInFlight = false;
  }
}

$("books").addEventListener("click", (event) => {
  const deleteButton = event.target.closest("[data-delete-book]");
  if (deleteButton) {
    deleteBookFromShelf(deleteButton.dataset.deleteBook).catch(showError);
    return;
  }
  const button = event.target.closest("[data-book]");
  if (button) selectBook(button.dataset.book).catch(showError);
});

$("chunks").addEventListener("click", (event) => {
  const button = event.target.closest("[data-chunk]");
  if (button) selectChunk(button.dataset.chunk).catch(showError);
});

$("text").addEventListener("mouseup", () => {
  updateSelectionAction();
});

$("text").addEventListener("touchend", () => {
  setTimeout(updateSelectionAction, 80);
});

$("text").addEventListener("click", (event) => {
  const mark = event.target.closest("mark[data-note-id]");
  if (mark) activateAnnotation(mark.dataset.noteId, { scroll: true });
});

document.addEventListener("selectionchange", updateSelectionAction);

$("cancel-note").addEventListener("click", () => {
  $("note-form").hidden = true;
});

$("note-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const note = $("note").value.trim();
  if (!note) return;
  await api("/api/annotations", {
    method: "POST",
    body: {
      bookId: state.bookId,
      chunkId: state.chunkId,
      quote: state.quote,
      quoteOffset: state.quoteOffset,
      note,
      kind: "note",
    },
  });
  $("note-form").hidden = true;
  window.getSelection()?.removeAllRanges();
  updateSelectionAction();
  await refreshCurrent({ force: true });
});

$("note-selection").addEventListener("click", () => {
  const quote = state.selectedQuote || window.getSelection()?.toString() || "";
  openNoteForm(quote);
});

$("margins").addEventListener("click", (event) => {
  if (event.target.closest("textarea, button, .comments")) return;
  const card = event.target.closest(".note-card[data-note-id], .collect-row[data-note-id]");
  if (card) activateAnnotation(card.dataset.noteId);
});

// 「递给他」：收藏（或同章的私人笔记）走 submit-notes，他在下面回一句。
// 会把同章其他待发送的笔记一起带过去 —— 本来也是要给他的。
async function submitCollectToNox(noteId) {
  const note = state.annotations.find((item) => item.id === noteId);
  if (!note) return;
  showToast("递给他了 · 他在读这一段…");
  try {
    const result = await api("/api/submit-notes", {
      method: "POST",
      body: { bookId: note.bookId, chunkId: note.chunkId, sessionId: "reader", contextMode: "chunk-once-per-session" },
    });
    if (result.repliedCount) showToast("他回你了 · 在页边");
    else if (result.replyError) showToast(`他这会儿没回上来（${result.replyError}）`);
    else showToast(result.message || "已经递过去了。");
  } catch (error) {
    showToast(`没递上：${error.message || error}`);
  }
  await refreshCurrent({ force: true });
}

document.addEventListener("click", (event) => {
  const ask = event.target.closest("[data-collect-ask]");
  if (ask) {
    event.preventDefault();
    submitCollectToNox(ask.dataset.collectAsk).catch(showError);
  }
});

document.addEventListener("submit", async (event) => {
  const form = event.target.closest(".reply-form");
  if (!form) return;
  event.preventDefault();
  const textarea = form.querySelector("textarea");
  const note = textarea.value.trim();
  if (!note) return;
  await api("/api/replies", {
    method: "POST",
    body: {
      parentId: form.dataset.parentId,
      note,
      author: "user",
      kind: "reply",
    },
  });
  textarea.value = "";
  delete state.replyDrafts[form.dataset.parentId];
  await refreshCurrent({ force: true });
});

document.addEventListener("input", (event) => {
  const textarea = event.target.closest("textarea");
  const form = event.target.closest(".reply-form");
  if (!textarea || !form) return;
  state.replyDrafts[form.dataset.parentId] = textarea.value;
});

document.addEventListener("compositionstart", (event) => {
  if (!event.target.closest?.(".reply-form, .note-form")) return;
  state.composing = true;
});

document.addEventListener("compositionend", (event) => {
  if (!event.target.closest?.(".reply-form, .note-form")) return;
  state.composing = false;
});

$("submit-notes").addEventListener("click", async () => {
  // 发送之后服务端会同步等他把回应写完（每条几秒），所以这里必须先给个反馈 ——
  // 否则按钮一动不动，看着像点了没反应。
  const button = $("submit-notes");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "他在看…";
  $("status").textContent = "笔记已经递过去了，他正在读…";

  try {
    const result = await api("/api/submit-notes", {
      method: "POST",
      body: {
        bookId: state.bookId,
        sessionId: "reader",
        contextMode: "chunk-once-per-session",
      },
    });
    await refreshCurrent({ force: true });

    if (!result.submissionId) {
      $("status").textContent = result.message || "没有可发送的私人笔记。";
    } else if (result.repliedCount) {
      const skipped = result.replySkipped
        ? `另外 ${result.replySkipped} 条这次没逐条回。`
        : "";
      $("status").textContent = `他在 ${result.repliedCount} 条笔记下回了话。${skipped}`;
    } else if (result.replyError) {
      // 笔记确实发出去了，只是这会儿没回上来 —— 两件事要分开说，
      // 不然她会以为笔记也没发成功
      $("status").textContent = `已发送 ${result.count} 条，但他这会儿没回上来（${result.replyError}）。稍后刷新看看。`;
    } else {
      $("status").textContent = `已发送 ${result.count} 条。`;
    }
  } catch (error) {
    $("status").textContent = `发送失败：${error.message || error}`;
  } finally {
    button.disabled = false;
    button.textContent = originalText;
    renderAnnotations();
  }
});

/* ---- 三键：收藏 / 写批注 / 问他 ---- */

$("collect-selection").addEventListener("click", async () => {
  const quote = state.selectedQuote;
  if (!quote || !state.bookId || !state.chunkId) return;
  try {
    await api("/api/annotations", {
      method: "POST",
      body: {
        bookId: state.bookId,
        chunkId: state.chunkId,
        quote,
        quoteOffset: state.selectedQuoteOffset,
        note: "",
        kind: "collect",
        author: "user",
        status: "open",
      },
    });
    window.getSelection()?.removeAllRanges();
    updateSelectionAction();
    bumpBadge(1);
    showToast("已收藏 · 整本书的收藏里（折痕 ✦）");
    await refreshCurrent({ force: true });
  } catch (error) {
    showError(error);
  }
});

// 问他 = 收下这句 + 立刻递给他。同章其他待发送的笔记会一起带过去。
$("ask-selection").addEventListener("click", async () => {
  const quote = state.selectedQuote;
  if (!quote || !state.bookId || !state.chunkId) return;
  $("status").textContent = "递给他了 · 他在读这一段…";
  try {
    await api("/api/annotations", {
      method: "POST",
      body: {
        bookId: state.bookId,
        chunkId: state.chunkId,
        quote,
        quoteOffset: state.selectedQuoteOffset,
        note: "这一段想听你说说。",
        kind: "note",
        author: "user",
        status: "open",
      },
    });
    window.getSelection()?.removeAllRanges();
    updateSelectionAction();
    const result = await api("/api/submit-notes", {
      method: "POST",
      body: { bookId: state.bookId, chunkId: state.chunkId, sessionId: "reader", contextMode: "chunk-once-per-session" },
    });
    if (result.repliedCount) $("status").textContent = "他回你了 · 在页边";
    else if (result.replyError) $("status").textContent = `已递过去，但他这会儿没回上来（${result.replyError}）。`;
    else $("status").textContent = result.message || "已递过去。";
    await refreshCurrent({ force: true });
  } catch (error) {
    showError(error);
  }
});

/* ---- 前情细条：隔了 7 天以上回来，开卷前他能给你备一份前情 ---- */

function maybeShowRecapBanner(book) {
  const banner = $("recap-banner");
  banner.hidden = true;
  if (!book || !book.lastReadAt || !book.chunksRead) return;
  const days = Math.floor((Date.now() - Date.parse(book.lastReadAt)) / 86_400_000);
  if (days < 7) return;
  const lastChunk = state.chunks.find((chunk) => chunk.id === book.lastChunkId);
  $("recap-banner-text").textContent = `距你上次翻开已 ${days} 天 · 读到「${lastChunk?.title || "开头"}」`;
  const go = $("recap-go");
  go.hidden = false;
  go.disabled = false;
  go.textContent = "让他给我讲讲前情";
  banner.hidden = false;
}

$("recap-go").addEventListener("click", async () => {
  const go = $("recap-go");
  go.disabled = true;
  go.textContent = "生成中";
  $("recap-banner-text").textContent = "他在翻你划过的地方…";
  try {
    const result = await api("/api/recap", { method: "POST", body: { bookId: state.bookId } });
    bumpBadge(1);
    showToast(result.reused ? "前情本来就在卡册里（折痕 ✦）" : "前情落进折痕了（✦ 前情）");
    $("recap-banner-text").textContent = "前情落进折痕了";
    go.hidden = true;
    setTimeout(() => {
      $("recap-banner").hidden = true;
    }, 2600);
  } catch (error) {
    $("recap-banner-text").textContent = "前情这会儿没生成出来";
    go.disabled = false;
    go.textContent = "再试一次";
    showError(error);
  }
});

$("recap-dismiss").addEventListener("click", () => {
  $("recap-banner").hidden = true;
});

/* ---- 折痕浮标 + 抽屉 ---- */

$("fab-fold").addEventListener("click", async () => {
  const opening = $("drawer-wrap").hidden;
  $("intercom").hidden = true;
  $("drawer-wrap").hidden = !opening;
  if (opening) {
    await loadDrawer().catch(showError);
    resetBadge();
  }
});

$("drawer-dim").addEventListener("click", () => {
  $("drawer-wrap").hidden = true;
});

for (const [tab, panel] of [
  ["tab-collects", "panel-collects"],
  ["tab-cards", "panel-cards"],
  ["tab-recap", "panel-recap"],
]) {
  $(tab).addEventListener("click", () => switchDrawerTab(tab));
}

$("collect-list").addEventListener("click", (event) => {
  if (event.target.closest("button")) return; // 递给他有自己的委托
  const row = event.target.closest("[data-jump-chunk]");
  if (!row) return;
  const chunkId = row.dataset.jumpChunk;
  const quote = row.dataset.jumpQuote || "";
  $("drawer-wrap").hidden = true;
  const jump = state.chunkId === chunkId ? Promise.resolve() : selectChunk(chunkId);
  jump.then(() => scrollToQuote(quote)).catch(showError);
});

/* ---- 边读边聊：语音（设备识别 + 设备朗读） + 打字，位置自动带上 ---- */

const intercom = { recognition: null, listening: false };

function icLine(cls, who, text) {
  const p = document.createElement("p");
  p.className = `ic-line ${cls}`;
  if (who) {
    const name = document.createElement("span");
    name.className = "ic-who";
    name.textContent = `${who}：`;
    p.appendChild(name);
  }
  p.appendChild(document.createTextNode(text));
  $("ic-body").appendChild(p);
  $("ic-body").scrollTop = $("ic-body").scrollHeight;
}

function icSetState(dotClass, label) {
  $("ic-dot").className = `ic-dot ${dotClass || ""}`.trim();
  $("ic-status").textContent = label;
}

function zhSpeechVoice() {
  if (!("speechSynthesis" in window)) return null;
  return (
    speechSynthesis.getVoices().find((voice) => voice.lang?.toLowerCase().startsWith("zh")) || null
  );
}

function speakReply(text) {
  if (!("speechSynthesis" in window)) return;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "zh-CN";
  const voice = zhSpeechVoice();
  if (voice) utterance.voice = voice;
  speechSynthesis.cancel();
  speechSynthesis.speak(utterance);
}

async function intercomSend(text) {
  const message = String(text || "").trim();
  if (!message) return;
  icLine("me", "你", message);
  icSetState("think", "他在想…");
  $("ic-input").value = "";
  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: {
        bookId: state.bookId,
        chunkId: state.chunkId,
        text: message,
        quote: state.selectedQuote || undefined,
      },
    });
    icLine("him", "他", result.reply);
    icSetState("", "边读边聊 · 他听得见你读到哪");
    speakReply(result.reply);
  } catch (error) {
    icLine("err", "", `没接上：${error.message || error}`);
    icSetState("", "边读边聊");
  }
}

$("ic-form").addEventListener("submit", (event) => {
  event.preventDefault();
  intercomSend($("ic-input").value);
});

$("ic-mic").addEventListener("click", () => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    showToast("这台设备不支持语音识别，先打字（朗读照常）");
    return;
  }
  if (intercom.listening) {
    intercom.recognition?.stop();
    return;
  }
  const recognition = new SR();
  recognition.lang = "zh-CN";
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;
  intercom.recognition = recognition;
  intercom.listening = true;
  $("ic-mic").classList.add("listening");
  icSetState("listen", "听着…（说完自己会停）");
  recognition.onresult = (event) => {
    let final = "";
    let interim = "";
    for (const result of event.results) {
      if (result.isFinal) final += result[0].transcript;
      else interim += result[0].transcript;
    }
    $("ic-input").value = final || interim;
    if (final) {
      intercom.listening = false;
      $("ic-mic").classList.remove("listening");
      intercomSend(final);
    }
  };
  recognition.onerror = () => {
    intercom.listening = false;
    $("ic-mic").classList.remove("listening");
    icSetState("", "边读边聊 · 语音没听清，可以再说或打字");
  };
  recognition.onend = () => {
    intercom.listening = false;
    $("ic-mic").classList.remove("listening");
    if (!$("ic-input").value) icSetState("", "边读边聊 · 他听得见你读到哪");
  };
  recognition.start();
});

$("fab-mic").addEventListener("click", () => {
  const opening = $("intercom").hidden;
  $("drawer-wrap").hidden = true;
  $("intercom").hidden = !opening;
  if (opening) {
    icSetState("", "边读边聊 · 他听得见你读到哪");
    if ("speechSynthesis" in window) speechSynthesis.getVoices(); // 触发声表加载
  } else if ("speechSynthesis" in window) {
    speechSynthesis.cancel();
  }
});

$("ic-close").addEventListener("click", () => {
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  intercom.recognition?.stop();
  $("intercom").hidden = true;
});

/* ---- 读到章底也算读过（翻章之外的另一条自动已读路） ---- */

let bottomTimer = null;
function onMaybeReachBottom() {
  if (!state.chunkId || !$("drawer-wrap").hidden || !$("intercom").hidden) return;
  const nearBottom =
    window.innerHeight + window.scrollY >= document.body.offsetHeight - 140;
  const reader = document.querySelector(".reader");
  const innerNear =
    reader && reader.scrollHeight > reader.clientHeight + 40 &&
    reader.scrollTop + reader.clientHeight >= reader.scrollHeight - 140;
  if (!nearBottom && !innerNear) return;
  clearTimeout(bottomTimer);
  bottomTimer = setTimeout(() => autoMarkRead(state.chunkId), 2500);
}
window.addEventListener("scroll", onMaybeReachBottom, { passive: true });
document.querySelector(".reader")?.addEventListener("scroll", onMaybeReachBottom, { passive: true });

$("continue-reading").addEventListener("click", async () => {
  if (!state.bookId) return;
  const next = await api(`/api/continue?bookId=${encodeURIComponent(state.bookId)}`);
  const chunkId = next?.chunk?.chunk?.id || next?.chunk?.chunkId || next?.chunk?.id;
  if (!chunkId) {
    $("status").textContent = next?.message || "没有更多可继续的了。";
    return;
  }
  await selectChunk(chunkId);
});

$("refresh").addEventListener("click", () => refreshCurrent({ force: true }).catch(showError));

$("import-book").addEventListener("click", () => {
  $("import-file").click();
});

$("import-file").addEventListener("change", async (event) => {
  const files = Array.from(event.target.files || []);
  if (!files.length) return;
  $("import-book").disabled = true;
  try {
    const imported = [];
    for (const file of files) {
      $("status").textContent = `正在导入 ${file.name}...`;
      const manifest = await api("/api/import", {
        method: "POST",
        body: {
          filename: file.name,
          dataBase64: await fileToBase64(file),
        },
      });
      imported.push(manifest);
    }
    $("status").textContent = files.length === 1 ? `已导入 ${files[0].name}。` : `已导入 ${files.length} 本书。`;
    await loadBooks();
    renderBooks();
    if (imported.length === 1 && imported[0]?.bookId) {
      await selectBook(imported[0].bookId);
    }
  } catch (error) {
    showError(error);
  } finally {
    $("import-book").disabled = false;
    event.target.value = "";
  }
});

function showError(error) {
  $("status").textContent = error.message || String(error);
}

loadBooks().catch(showError);
setInterval(() => {
  if (document.hidden) return;
  refreshCurrent().catch(showError);
}, 5000);
