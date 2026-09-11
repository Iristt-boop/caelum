# 一次性修复脚本：把 Movies 组件的 return 块整体替换为配平的版本
import io

path = r"D:\claude-code\nox-app\caelum-os-ui\src\pages\Movies.jsx"
lines = io.open(path, encoding="utf-8").read().split("\n")

NEW = r'''  return (
    <div className={`flex h-full flex-col ${theater ? "" : "pb-3"}`}>
      <style>{DANMAKU}</style>
      {!theater && (
        <PageHead title="Movies" sub="把片子贴进来，他坐在你旁边陪你看。" />
      )}

      {/* 剧场 = 行 flex：画面圆角盒 + 右栏（聊天 2/3 + 控制 1/3）。
          平铺 = 列 flex：居中的 16:9 画面盒 + 一起看过。
          ⚠️ 画面盒是同一个 JSX 节点（key/父链不变），只换 className —— video 不重挂 */}
      <div className={`flex min-h-0 flex-1 ${theater ? "gap-2.5 p-2.5" : "relative flex-col gap-3"}`}>
        {/* ---- 画面 ---- */}
        <div
          className={
            theater ? "relative min-h-0 flex-1" : "flex min-h-0 flex-1 items-center justify-center"
          }
        >
          <div
            key="player"
            className={
              theater
                ? "relative h-full w-full overflow-hidden rounded-2xl bg-black"
                : `relative aspect-video max-h-full w-full overflow-hidden rounded-2xl border border-line ${
                    session ? "bg-black" : "glass"
                  }`
            }
            style={theater ? undefined : { boxShadow: "var(--shadow)" }}
          >
            {!session ? (
              <div className="relative flex h-full items-center justify-center overflow-hidden p-8">
                {art.ready ? (
                  <img
                    src={art.src}
                    alt=""
                    draggable={false}
                    className="pointer-events-none absolute inset-0 h-full w-full select-none object-cover"
                  />
                ) : (
                  <>
                    <Glow color="var(--accent)" size={300}
                          className="-left-16 -top-20 opacity-40" />
                    <Glow color="var(--accent-2)" size={260}
                          className="-bottom-24 -right-12 opacity-35" />
                  </>
                )}

                <div
                  className={`relative w-full max-w-md ${
                    art.ready ? "rounded-2xl bg-[var(--bg-base)]/72 p-5 backdrop-blur-md" : ""
                  }`}
                >
                  {!art.ready && (
                    <ThemedIcon name="popcorn" size={76} className="mx-auto mb-3 drop-shadow-sm" />
                  )}

                  <h2 className="text-center text-[15px] font-medium text-ink">
                    一起看一部片
                  </h2>
                  <p className="mt-1 text-center text-[12px] text-ink-3">
                    贴个链接，他坐在你旁边；硬盘里的片子也行
                  </p>
                  <div className="mt-4 flex gap-2">
                    <input
                      value={link}
                      onChange={(e) => setLink(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && doImport()}
                      placeholder="https://www.bilibili.com/video/…"
                      className="min-w-0 flex-1 rounded-xl border border-line bg-[color-mix(in_srgb,var(--text-1)_5%,transparent)]
                                 px-3.5 py-2.5 text-[13px] text-ink outline-none
                                 placeholder:text-ink-3 focus:border-[var(--accent)]"
                    />
                    <button
                      type="button"
                      onClick={() => doImport()}
                      disabled={importing || !link.trim()}
                      className="shrink-0 rounded-xl bg-accent px-4 py-2.5 text-[13px] font-medium text-white transition-opacity disabled:opacity-40"
                    >
                      {importing ? "解析中…" : "开始看"}
                    </button>
                  </div>
                  {err && <p className="mt-2 text-center text-[12px] text-[var(--accent-2)]">{err}</p>}

                  {local.canPlayLocal() && (
                    <div className="mt-4 flex items-center gap-3">
                      <span className="h-px flex-1 bg-line" />
                      <button
                        type="button"
                        onClick={openLocal}
                        className="flex shrink-0 items-center gap-1.5 rounded-xl border border-line px-3.5 py-2
                                   text-[12px] text-ink-2 transition-colors
                                   hover:border-[var(--accent)] hover:bg-accent-soft hover:text-ink"
                      >
                        <Icon name="folder" size={13} />
                        看硬盘里的片子
                      </button>
                      <span className="h-px flex-1 bg-line" />
                    </div>
                  )}

                  <p className="mt-3 text-center text-[11px] leading-relaxed text-ink-3">
                    原版电影配外挂字幕？开始后点「字幕」传 .srt 文件。
                    {local.canPlayLocal() && "　本地片子不上传，只在问画面时传一帧。"}
                  </p>
                </div>
              </div>
            ) : (
              <>
                {isIframe ? (
                  <iframe
                    src={session.embed_url}
                    title={session.title}
                    className="h-full w-full border-0 bg-black"
                    allowFullScreen
                    allow="autoplay; fullscreen; encrypted-media"
                  />
                ) : (
                  <video
                    ref={videoRef}
                    src={isLocal ? session.src : watch.streamUrl(session.session_id)}
                    controls
                    autoPlay
                    className={`h-full w-full ${fitCls}`}
                    onPause={() => setPaused(true)}
                    onPlay={() => setPaused(false)}
                  />
                )}
                {!isIframe && !isLocal && session.separate_audio && (
                  <audio
                    ref={audioRef}
                    src={watch.streamUrl(session.session_id, "audio")}
                    preload="auto"
                  />
                )}

                {/* 弹幕层 */}
                {!isIframe && (
                  <div ref={dkLayerRef}
                       className="pointer-events-none absolute inset-x-0 top-0 h-[70%] overflow-hidden">
                    {dk && (
                      <div key={dk.id} className="dk-item"
                           style={danmakuStyle(dkLayerRef.current, dk.top)}>
                        {dk.text}
                      </div>
                    )}
                  </div>
                )}

                {/* 当前字幕。暂停也显示（校同步要看字）；bottom-14 躲开原生控制条 */}
                {!isIframe && curSub && (
                  <div className="pointer-events-none absolute inset-x-0 bottom-14 flex justify-center px-6">
                    <p className="max-w-[85%] text-center leading-snug text-white [text-shadow:0_1px_3px_rgba(0,0,0,.95),0_0_8px_rgba(0,0,0,.6)]"
                       style={{ fontSize: subPx }}>
                      {curSub}
                    </p>
                  </div>
                )}

                {/* 剧场顶栏（常驻 —— iframe 模式唤出不成立，见 git 历史） */}
                {theater && (
                  <div
                    className="absolute inset-x-0 top-0 z-30 flex h-9 items-center gap-2 px-3"
                    style={{ background: "linear-gradient(to bottom, rgba(0,0,0,.72), transparent)" }}
                  >
                    <span className="truncate text-[12px] text-white/90">
                      {session.title}
                      {session.episode ? <span className="text-white/55"> · {session.episode}</span> : null}
                    </span>
                    {isIframe && (
                      <span className="shrink-0 rounded border border-white/25 px-1.5 py-px text-[10px] text-white/60">
                        番剧模式 · 他看不到画面
                        {session.notes?.length ? `｜B站标了：${session.notes.join("、")}` : ""}
                      </span>
                    )}
                    <div className="flex-1" />
                    <button
                      type="button"
                      onClick={() => setChatOpen((v) => !v)}
                      className="shrink-0 rounded-lg px-2 py-1 text-[11.5px] text-white/80 transition-colors hover:bg-white/15 hover:text-white"
                    >
                      {chatOpen ? "收起聊天" : "和他聊天"}
                      {thread.length > 0 && !chatOpen && (
                        <span className="ml-1 font-mono text-[10px] text-white/50">{thread.length}</span>
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => setTheater(false)}
                      title="Esc"
                      className="shrink-0 rounded-lg px-2 py-1 text-[11.5px] text-white/80 transition-colors hover:bg-white/15 hover:text-white"
                    >
                      退出剧场
                    </button>
                  </div>
                )}

                {/* 平铺模式：右侧聊天卡（悬浮圆角）+ 收起后的重开入口 */}
                {!theater && chatOpen && (
                  <div
                    className="absolute inset-y-2 right-2 z-20 flex w-[min(320px,46%)] flex-col overflow-hidden rounded-2xl border border-line"
                    style={{
                      background: "color-mix(in srgb, var(--bg-base) 94%, transparent)",
                      backdropFilter: "blur(14px)",
                      boxShadow: "var(--shadow)",
                    }}
                  >
                    <ChatBody
                      session={session}
                      thread={thread}
                      question={question}
                      setQuestion={setQuestion}
                      asking={asking}
                      paused={paused}
                      onAsk={ask}
                      isIframe={isIframe}
                      atTime={atTime}
                      setAtTime={setAtTime}
                      timeSet={timeSet}
                      onReport={reportTime}
                      onClose={() => setChatOpen(false)}
                    />
                  </div>
                )}
                {!theater && !chatOpen && session && (
                  <button
                    type="button"
                    onClick={() => setChatOpen(true)}
                    className="absolute bottom-3 right-3 z-20 flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[11.5px] text-white backdrop-blur transition-colors hover:text-white"
                    style={{ background: "rgba(15,10,20,.55)" }}
                  >
                    <span className="relative flex h-1.5 w-1.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60"
                            style={{ background: "var(--accent)" }} />
                      <span className="relative inline-flex h-1.5 w-1.5 rounded-full" style={{ background: "var(--accent)" }} />
                    </span>
                    和 Nox 聊天{thread.length > 0 ? ` · ${thread.length}` : ""}
                  </button>
                )}
              </>
            )}
          </div>
        </div>

        {/* ---- 剧场右栏：聊天卡（2/3 高）+ 影院控制卡（1/3）。
            糖糖 2026-08-28：控制条不压画面，全挪右侧；圆角走 Design 规范 ---- */}
        {theater && session && (
          <div className="flex w-[330px] shrink-0 flex-col gap-2.5">
            {chatOpen && (
              <div
                className="flex min-h-0 flex-[2] flex-col overflow-hidden rounded-2xl border border-line"
                style={{
                  background: "color-mix(in srgb, var(--bg-base) 94%, transparent)",
                  backdropFilter: "blur(14px)",
                }}
              >
                <ChatBody
                  session={session}
                  thread={thread}
                  question={question}
                  setQuestion={setQuestion}
                  asking={asking}
                  paused={paused}
                  onAsk={ask}
                  isIframe={isIframe}
                  atTime={atTime}
                  setAtTime={setAtTime}
                  timeSet={timeSet}
                  onReport={reportTime}
                  onClose={() => setChatOpen(false)}
                />
              </div>
            )}
            {!isIframe && (
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-line">
                <div className="flex shrink-0 items-center gap-1.5 px-3 pt-2.5 text-[11px] font-medium tracking-wide text-ink-3">
                  <Icon name="gear" size={12} /> 影院控制
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto p-2.5">
                  <CinemaChips
                    companion={companion} setCompanion={setCompanion}
                    onPickSub={() => subInputRef.current?.click()}
                    subtitles={subtitles} subsName={subsName}
                    fitLabel={FIT_MODES[fitIx].label}
                    onCycleFit={() => setFitIx((i) => (i + 1) % FIT_MODES.length)}
                    subSizeLabel={SUB_SIZES[subSizeIx].label}
                    onCycleSubSize={() => setSubSizeIx((i) => (i + 1) % SUB_SIZES.length)}
                    subOffset={subOffset}
                    setSubOffset={setSubOffset}
                    syncOpen={syncOpen} setSyncOpen={setSyncOpen}
                    streamSid={!isIframe && !isLocal ? session.session_id : null}
                    onNewFilm={() => { setSession(null); setTheater(false); }}
                    onTheater={() => setTheater(true)}
                    inTheater={theater}
                  />
                  {err && (
                    <p className="mt-2 rounded-lg px-2.5 py-1.5 text-[11px] text-white"
                       style={{ background: "rgba(160,40,40,.8)" }}>
                      {err}
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ---- 没片子时：一起看过（有封面的出海报卡，没有的走简洁文字行）---- */}
        {!theater && !session && (
          <Panel key="watched" className="flex max-h-[260px] shrink-0 flex-col">
            <div className="flex items-center gap-1.5 px-3 pt-2.5 text-[11px] font-medium tracking-wide text-ink-3">
              <Icon name="history" size={12} /> 一起看过
            </div>
            <WatchedStrip onReplay={doImport} />
          </Panel>
        )}
      </div>

      {/* 字幕文件上传的隐藏 input。放页面层 —— 它不属于任何一种布局 */}
      <input
        ref={subInputRef}
        type="file"
        accept=".srt,.vtt,text/plain"
        className="hidden"
        onChange={onSubFile}
      />
    </div>
  );'''

start = None
for i, l in enumerate(lines):
    if l.strip() == "return (":
        start = i
        break
end = None
for n in range(start + 1, len(lines)):
    if lines[n] == "}":
        end = n
        break
assert start is not None and end is not None, (start, end)
out = lines[:start] + NEW.split("\n") + lines[end:]
io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(out))
print("rewritten: return 行", start + 1, "到", end + 1, "替换为新块", len(NEW.split("\n")), "行")
