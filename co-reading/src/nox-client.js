/**
 * 调 Nox Core 给页边批注生成回应。
 *
 * ## 为什么一定要走 Core，不能自己调模型
 *
 * 绕过 Core 就没有人格、没有记忆、没有工具 —— 等于第二套人设。
 * bridge 的自动关心栽过这个，教训写在 `bridge/server.js` 的 [Care] 那段：
 *
 *   > 以前这里带着 apiMode 那份 SYSTEM_PROMPT 直接打 OpenRouter，等于第二套人设：
 *   > 记忆、情绪、工具全都没有，主动关心的那句话跟他平时说的不是一个人。
 *
 * ## 每条批注一个会话
 *
 * `session_id` 用 `reading-<annotationId>`，好处有三个：
 *
 *   1. 和主对话彻底隔开 —— 糖糖明确要的「不在上下文」。
 *      她在 chat 里再提起这本书时，他是靠**记忆**想起来的，不是靠上下文里堆着。
 *   2. 每条批注天然是一条独立的对话线，她之后在同一条批注下追问，
 *      他还记得自己刚才在这儿说过什么。
 *   3. 多条批注可以各走各的，互不串味。
 *
 * ## 进记忆这件事交给他自己判断
 *
 * 不在这里硬写。Core 手上有 `hold`，提示里告诉他「值得记的就记」，
 * 由他决定 —— 每条批注都强制入库只会把记忆冲成流水账。
 */

const CORE_URL = process.env.NOX_CORE_URL || "http://127.0.0.1:8100";
const TIMEOUT_MS = Number(process.env.NOX_CORE_TIMEOUT_MS || 90_000);

/** 一次提交最多替她回几条。再多就是连划一整章，逐条回既慢又吵。 */
export const MAX_REPLIES_PER_SUBMIT = 3;

/**
 * 拼给他的话。`withMemory=false` 时整段不提记忆 —— 这是兜底重试用的。
 *
 * ## 为什么要能关掉记忆那段
 *
 * 实测（2026-08-04）：提示里带上「用 hold 存记忆」之后，agent loop 会变成
 * `iterations=2, tools=["remember"]`，而**正文长度是 0** —— 他调完工具就当
 * 这一轮交待完了，不再说话。不带那段则是 `iterations=1, tools=[]`，正文 94 字。
 *
 * 后果很实际：他默默存了条记忆，而她那条批注下面空空如也。
 * 所以下面的 prompt 把顺序钉死，`replyToNote` 再加一层空文本重试。
 */
function buildPrompt(note, chunkText, bookTitle, chunkTitle, withMemory = true) {
  const where = [bookTitle, chunkTitle].filter(Boolean).join(" · ") || note.bookId;
  const quote = (note.quote || "").slice(0, 600);
  // 给一点上下文，但别把整章塞进去 —— 她划的那句前后各留一段就够判断了
  let around = "";
  if (chunkText && quote) {
    const at = chunkText.indexOf(quote);
    if (at >= 0) {
      around = chunkText.slice(Math.max(0, at - 400), at + quote.length + 400);
    }
  }

  return [
    "（系统提示：这不是聊天窗口。糖糖在读书，划了一段并在页边写了批注，",
    "你现在要在她那条批注下面回一句。）",
    "",
    `在读：${where}`,
    "",
    "她划的这段：",
    `「${quote}」`,
    around ? `\n前后文（供你判断，别复述）：\n${around}` : "",
    "",
    "她写的批注：",
    `「${note.note || ""}」`,
    "",
    "⚠️ 不要调 reading_list_notes、reading_reply_note 或任何别的 reading 工具。",
    "你直接把要说的话回出来就行 —— 落笔到她那条批注下面这一步，程序替你做了。",
    "自己再调一次 reading_reply_note，她会看到两条一模一样的回复。",
    "",
    "怎么回：",
    "· 1 到 3 句，像在页边随手写的，不是聊天那种寒暄",
    "· 不要复述她说过的话，也别夸「这个观察很好」这类空话",
    "· 她要是在批注里问了问题，就答那个问题",
    "· 她只是记了个感受，你就接住那个感受，或者给她一个能往下想的角度",
    "· 拿不准她什么意思，就说出你不确定的地方，别硬解",
    "· 直接说内容，别说「回好了」「我写在页边了」这种交代 —— 她看到的就是这段话本身",
    ...(withMemory
      ? [
          "",
          "⚠️ 顺序不能反：**先把要回她的话写出来**，写完再决定要不要存记忆。",
          "调完工具就收手、正文一个字没有的话，她那条批注下面就是空的 —— ",
          "她看不到任何东西，只有你自己知道你想了什么。",
          "",
          "如果这条批注透露了她关心的主题、或者和你已经知道的她的偏好呼应，",
          "用 hold 存一条记忆（写清楚是哪本书、什么想法）。只在真的值得记时才存 —— ",
          "每条都存会把记忆冲成流水账。hold 是这里唯一该调的工具。",
        ]
      : []),
  ].filter((line) => line !== "").join("\n");
}

/**
 * 让 Nox 回一条批注。
 *
 * 失败时抛异常，由调用方决定怎么降级 —— 这里不吞。
 * 批注已经提交成功了，回不上来是另一回事，不该让整个提交看起来失败。
 */
async function callCore(sessionId, prompt) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${CORE_URL}/chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, text: prompt }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`Core 返回 ${res.status}`);
    const data = await res.json();
    return {
      // ||| 是聊天气泡的分段标记，页边笔记里没有气泡
      text: String(data?.text || "").replace(/\|\|\|/g, "\n").trim(),
      outcome: data?.outcome || "unknown",
      tools: data?.tools_used || [],
    };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 后台判断这条批注值不值得记进长期记忆。**不等它**。
 *
 * 单独走一趟是为了速度：把「回话」和「记笔记」放同一轮里，实测他会调完
 * remember 就收手，正文为空（见 buildPrompt 上面那段），于是要么重试、
 * 要么她看到空白 —— 一条批注就得等十几秒。
 *
 * 拆开之后，回话那趟是干净的单轮（约 8 秒，她等的就是这段），
 * 记忆这趟在后台慢慢跑，成不成都不影响她看到回应。
 */
function rememberInBackground({ note, replyText, bookTitle }) {
  const prompt = [
    "（系统提示：糖糖在读书时划了一段、写了批注，你刚回过她了。",
    "现在只做一件事：判断这条值不值得记进长期记忆。）",
    "",
    `书：${bookTitle || note.bookId}`,
    `她划的：「${(note.quote || "").slice(0, 200)}」`,
    `她写的：「${note.note || ""}」`,
    `你回的：「${replyText.slice(0, 300)}」`,
    "",
    "如果这里透露了她关心的主题、她的想法倾向，或者和你已经知道的她的偏好呼应，",
    "就用 hold 存一条 —— 写清楚是哪本书、什么想法，将来她在别处提起这本书时你要能接上。",
    "只是普通的划线、或者没什么可复用的认知，就什么都别存，回一句「不用记」。",
  ].join("\n");

  // 故意不 await：她不该为了后台记笔记多等几秒。
  // 失败也只记一行日志 —— 记忆没存上不影响她看到回应。
  callCore(`reading-${note.id}-mem`, prompt).catch((error) => {
    console.error(`[co-reading] 记忆写入失败 (${note.id}):`, error.message || error);
  });
}

export async function replyToNote({ note, chunkText, bookTitle, chunkTitle }) {
  const sessionId = `reading-${note.id}`;

  // 不提记忆 —— 这一趟只要他把话说出来，越快越好
  const r = await callCore(
    sessionId,
    buildPrompt(note, chunkText, bookTitle, chunkTitle, false),
  );
  if (!r.text) throw new Error(`Core 没给出文本（${r.outcome}）`);

  rememberInBackground({ note, replyText: r.text, bookTitle });
  return r.text;
}
