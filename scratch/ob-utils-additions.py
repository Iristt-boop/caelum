# ── 要插进 Ombre-Brain 的 utils.py 的两个函数（排期 4.1 / 4.2）──
#
# ⚠️ **这个文件是唯一真源。** `scratch/apply-ob-durability.py` 从这里读，
#    原样贴进 VPS 上的 `utils.py`。别在两边各改一份。
#
# 本地可测：`python scratch/test-ob-utils-additions.py`
# （纯 stdlib，不需要 frontmatter / yaml，所以本机跑得动 ——
#   OB 本体在本机跑不了测试，但这两个函数可以。）

import os
import sqlite3
import tempfile


def write_atomic(path, text: str, encoding: str = "utf-8") -> None:
    """原子地写一个文本文件（排期 4.2）。

    ## 它治的是什么

    原来全仓是这个写法：

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(frontmatter.dumps(post))

    `open(..., "w")` **先把文件截成 0 字节**，再往里写。也就是说从截断到
    写完这中间，磁盘上那个桶是**空的**。这期间进程被杀 / 机器断电 / 磁盘满，
    那条记忆就**永久没了 —— 不报错、不留痕**。

    桶目录里有 249 个桶，写者不止一个（server 的 hold/grow、decay_engine、
    dehydrator、reclassify 脚本…），谁都可能在别人写到一半时被打断。

    ## 做法

    同目录写临时文件 → `fsync` → `os.replace`。
    `os.replace` 在同一个文件系统上是**原子的**：要么看到旧的完整内容，
    要么看到新的完整内容，**不存在"看到一半"**。

    ## 三个容易漏的点

    1. **临时文件必须在同一目录** —— 跨文件系统 `os.replace` 不是原子的，
       会退化成"拷贝+删除"。
    2. **后缀不能是 `.md`** —— `bucket_manager.py` 是用 `endswith(".md")`
       枚举桶的。留下一个半截的 `.md` 临时文件，下一次扫描会**把它当成一条记忆**。
    3. **写完要 fsync 再 replace** —— 不 fsync 的话，断电后可能
       rename 已经落盘、内容还在页缓存里，结果是一个**空文件顶掉了好的那个**。
       比不改还糟。
    """
    path = os.fspath(path)
    directory = os.path.dirname(path) or "."
    # 前缀点 + 非 .md 后缀：既不被 `endswith(".md")` 扫到，也一眼看得出是临时的
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".ob-tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None  # 已经改名了，下面别再删
    finally:
        if tmp is not None:
            # 写到一半失败：把残骸清掉，**旧文件一个字节都没动**
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # 目录项也落一次盘 —— 不做的话，断电后可能"新文件有了但改名没生效"。
    # 某些文件系统不允许 fsync 目录，失败就算了（内容本身已经 fsync 过）。
    try:
        dfd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def connect_db(path, timeout: float = 5.0) -> sqlite3.Connection:
    """开一个库连接，统一设好 PRAGMA（排期 4.1）。

    ## 为什么要收成一个函数

    原来 10 处 `sqlite3.connect(self.x_path)` 各开各的，**零 PRAGMA**。
    而 nox-core 那边三个库都设了 —— 同一套系统两套标准。

    ## 只有 WAL 是真缺的，`busy_timeout` 不是

    ⚠️ **2026-09-13 实测纠正**：`sqlite3.connect()` 的默认 `timeout=5.0`
    本来就等价于 `busy_timeout=5000`。所以这里显式写一遍**不修任何东西**，
    只是把意图写明、并挡住"有人加了 `timeout=0`"。
    （用 `sqlite3` 命令行读到的 `busy_timeout=0` 是**命令行自己那条连接**的值，
      不代表 OB 运行时 —— 别拿它当证据，我差点就拿了。）

    **真正缺的是 `journal_mode=WAL`**，它是**写在文件里、跨连接持久**的。
    实测两个库当时都是 `delete`：
      · 读会被写整个挡住（一次 embedding 写入就能让检索卡住）
      · 崩在事务中间要靠回滚日志恢复，比 WAL 脆
    """
    conn = sqlite3.connect(os.fspath(path), timeout=timeout)
    try:
        # WAL 是持久属性，设过一次就一直是。重复设是 no-op，不怕多调。
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.Error:
        # 设不上也要能用 —— 退回默认行为，总比连不上强。
        # 但不能静默：调用方看不见，只能靠日志。
        import logging

        logging.getLogger(__name__).warning(
            "给 %s 设 PRAGMA 失败，退回默认（读写会互相挡）", path
        )
    return conn
