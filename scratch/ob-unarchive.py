# ── 要插进 Ombre-Brain 的 unarchive（排期 4.3）──
#
# ⚠️ **这个文件是唯一真源。** `scratch/apply-ob-unarchive.py` 从这里读，
#    原样贴进 VPS 上的 `bucket_manager.py`。别在两边各改一份。
#
# 本地可测：`python scratch/test-ob-unarchive.py`
# （系统 python 有 frontmatter；nox-core 那个 venv 没有，别用它。）

import os
import shutil

import frontmatter

from utils import now_iso, safe_path, write_atomic


# ═══════════════════════════════════════════════════════════════════
# 下面这两个函数会被贴进 BucketManager（排期 4.3）
# ═══════════════════════════════════════════════════════════════════

async def unarchive(self, bucket_id: str) -> bool:
    """把归档的桶放回来 —— `archive()` 的逆操作（排期 4.3，2026-09-13）。

    ## 为什么必须有它

    在这之前**归档是单向的**：全仓 grep `unarchive` 零命中。
    而归档桶不参与浮现（`list_all(include_archive=False)` 不遍历 `archive/`），
    也不再衰减 —— **进去了就再也出不来**。

    实测线上有 6 条在归档里，其中：

        糖糖生日与星座       importance 3
        糖糖身高体重记录     importance 9   ← 最高档，浮现不出来

    衰减引擎会因为"很久没被碰"而归档，而"很久没被碰"和"不重要"
    根本是两回事 —— 她的身高体重就不会天天提。

    ## 🔴 必须刷新 `last_active`，否则这就是演戏

    衰减分数是
    `Importance × activation_count^0.3 × e^(-λ×days) × emotion_weight`，
    `days` 来自 `last_active`。一个桶之所以被归档，正是因为这个分数掉到了
    阈值以下。**放回来却不刷新 `last_active`，下一轮衰减立刻再把它归档**——
    从外面看就是"unarchive 没生效"，而且不报错。

    所以这里给它一个新的租期。这不是作弊：她（或他）主动把一条记忆捞回来，
    本身就是一次"碰"。

    ## 放回哪个目录

    按 `archived_from` 走（`archive()` 从今天起会记下原来的 type）。
    老的归档桶没有这个字段 —— 那时 `archive()` 把 `type` 直接覆盖成
    `archived`，**原来的类型当场就丢了**。对这些退回按 `pinned` 判断：
    钉选的回 `permanent/`，其余回 `dynamic/`。
    """
    file_path = self._find_bucket_file(bucket_id)
    if not file_path:
        return False

    # 必须**真的**在 archive/ 里。不在就如实返回 False ——
    # 对一个没归档的桶报"已恢复"，正是 4.3 另一半要修的那种谎。
    try:
        in_archive = os.path.commonpath(
            [os.path.realpath(file_path), os.path.realpath(self.archive_dir)]
        ) == os.path.realpath(self.archive_dir)
    except ValueError:      # 不同盘符（Windows）
        in_archive = False
    if not in_archive:
        return False

    try:
        post = frontmatter.load(file_path)

        # 回哪儿：archived_from > pinned > dynamic
        origin = post.get("archived_from")
        if origin not in ("permanent", "dynamic", "feel"):
            origin = "permanent" if (
                post.get("pinned") or post.get("protected")
            ) else "dynamic"

        target_root = {
            "permanent": self.permanent_dir,
            "dynamic": self.dynamic_dir,
            "feel": self.feel_dir,
        }[origin]

        domain = post.get("domain", ["未分类"])
        primary_domain = sanitize_name(domain[0]) if domain else "未分类"
        target_subdir = os.path.join(target_root, primary_domain)
        os.makedirs(target_subdir, exist_ok=True)
        dest = safe_path(target_subdir, os.path.basename(file_path))

        post["type"] = origin
        post.metadata.pop("archived_from", None)
        # 🔴 见上面那段：不刷新的话下一轮衰减会立刻再归档
        post["last_active"] = now_iso()

        write_atomic(file_path, frontmatter.dumps(post))
        shutil.move(file_path, str(dest))
    except Exception as e:
        logger.error(f"Failed to unarchive bucket / 取消归档失败: {bucket_id}: {e}")
        return False

    logger.info(f"Unarchived bucket / 取消归档: {bucket_id} → {origin}/{primary_domain}/")
    return True
