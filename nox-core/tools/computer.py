"""她电脑上那只手 —— 把 Caelum Gateway 的能力接成 Nox 的工具。

见 `D:\\claude-code\\CAELUM-HARNESS-ARCHITECTURE.md`。

传输在 `tools/local_link.py`，这边只管"给他看到什么、怎么描述"。
和 `tools/ha.py` 对 `tools/mcp_client.py` 是同一种分工。

## 🔴 这五件事发生在**她的电脑上**，不是服务器上

他很容易把这只手当成"服务器上的文件系统"来用 —— 那会读错东西，
更糟的是**写错东西**。所以每条描述里都点明「她电脑上」。

## 名字为什么和能力名不一样

Anthropic 的工具名只允许 `[a-zA-Z0-9_-]`，点号过不去。
所以对模型是 `computer_read_file`，发到链路上是 `computer.read_file`。
映射写在 `_CAPABILITY` 里，**别在两处各写一遍**。

## 写操作会被拦

`computer_write_file` / `computer_edit_file` 每次都要糖糖点头
（Gateway 的 policy.ts）。她可能：

  - 点「不行」   → 他该停下，换个说法问她，不是换个路径再试一次
  - 没看见       → 超时，回的是「取消」而不是「拒绝」

这两件事的区别在错误文本里，**描述里要写清楚**，否则他会把
「她没看见」理解成「她不同意」，然后不再提这件事了。
"""

from __future__ import annotations

import logging
from typing import Any

from agent.llm import ToolSpec
from tools.local_link import APPROVAL_TIMEOUT_S
from tools.untrusted import ingest

logger = logging.getLogger(__name__)


#: 要糖糖点头的那几件。**必须和 Gateway 的 `policy.ts` 保持一致** ——
#: 这边少列一个，那件事就会用 20 秒的默认超时，而她的弹窗有 60 秒，
#: 于是她还没看清路径这边就已经报超时了（2026-08-25 栽过）。
#:
#: ⚠️ 这里只影响「等多久」。**真正的权限判定在 Gateway**，
#: 不在这儿 —— 这边写漏了顶多是超时早，不会让谁绕过审批
_NEEDS_HER_NOD = frozenset({
    "computer_write_file",
    "computer_edit_file",
    "computer_run_command",
    #: 往常驻终端里打字 = 执行命令，和 run_command 完全同一件事
    #: （2026-08-28）。**Gateway 的 `ALWAYS_ASK` 里也有它** ——
    #: 那边才是真正把关的，这边只管「等她多久」
    "computer_terminal_send",
})


#: 工具名 → 链路上的能力名。**唯一一份**，加能力时只改这里。
_CAPABILITY: dict[str, str] = {
    "computer_read_file": "computer.read_file",
    "computer_find_files": "computer.find_files",
    "computer_search_files": "computer.search_files",
    "computer_write_file": "computer.write_file",
    "computer_edit_file": "computer.edit_file",
    "computer_run_command": "computer.run_command",
    #: 只读 git（2026-08-27）。命令行由 Gateway 拼死，自动放行 ——
    #: 他要提交/切分支仍然走 computer_run_command，那条每次问糖糖
    "computer_git_status": "computer.git_status",
    "computer_git_diff": "computer.git_diff",
    "computer_git_log": "computer.git_log",
    #: 浏览器只读（2026-08-27）。**他自己的浏览器，不是她的**
    "computer_browse": "computer.browse",
    #: 看图（2026-08-28）。⚠️ 这条**不走 `run`**，有自己的 handler ——
    #: 图片字节必须先过视觉模型，绝不能直接进他的上下文
    "computer_read_image": "computer.read_image",
    #: 常驻终端（2026-08-28）。`run_command` 是一次性的，起 dev server
    #: 会把那条调用永远卡住；这几件给他一个活着的 shell
    "computer_terminal_open": "computer.terminal_open",
    "computer_terminal_send": "computer.terminal_send",
    "computer_terminal_read": "computer.terminal_read",
    "computer_terminal_list": "computer.terminal_list",
    "computer_terminal_close": "computer.terminal_close",
}


#: Gateway 把 base64 藏在文本块里用的前缀。
#:
#: 🔴 **和 `image-tools.ts::IMAGE_MARK` 必须一模一样。**
#: 对不上的表现是：手明明读到了图，他却说"看不了" —— 而且不报错，
#: 因为两边各自都是对的。有测试盯着这个常量
_IMAGE_MARK = "CAELUM_IMAGE_B64:"


#: 🔴 **绝不往下传的参数。**
#:
#: `sandbox_permissions` 是 harness 那个 pwsh 工具的沙箱升级开关，
#: 取值里有 `danger-full-access` —— 字面意思就是不设防。
#:
#: Gateway 那边已经硬拦了（`policy.ts` 的 `sandbox_escape`），
#: 这里再挡一道**不是冗余**：模型可能被诱导着反复重试，
#: 每重试一次就在她的审计里留一条「他想突破沙箱」。
#: 在源头掐掉，那些请求根本不会发出去。
_NEVER_FORWARD = frozenset({"sandbox_permissions", "justification"})


#: 每条描述都带的一句。抽出来是为了**改一处就全改**，
#: 而不是五份各写一遍然后漂移
_ON_HER_PC = "在**糖糖的电脑上**（不是服务器）"


READ_SPEC = ToolSpec(
    side_effect="read",
    name="computer_read_file",
    description=(
        f"读一个文件的内容，{_ON_HER_PC}。"
        "路径要写全（比如 D:/claude-code/xxx.py）。"
        "文件很长时用 offset/limit 分段读，别一次拉完。"
        "\n不确定文件在哪就先用 computer_find_files，别猜路径。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "完整路径"},
            "offset": {"type": "number", "description": "从第几行开始（1 起），默认 1"},
            "limit": {"type": "number", "description": "最多读几行，默认 2000"},
        },
        "required": ["file_path"],
    },
)

FIND_SPEC = ToolSpec(
    side_effect="read",
    name="computer_find_files",
    description=(
        f"按文件名找文件，{_ON_HER_PC}。"
        "pattern 是 glob，比如 `**/*.py`、`src/**/*.test.js`。"
        "**不带 `/` 的模式匹配任意层级的文件名**，所以 `*.py` 是全树搜。"
        "\n找内容用 computer_search_files，这个只看文件名。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob 模式"},
            "path": {"type": "string", "description": "在哪个目录下找，不给就是默认工作区"},
        },
        "required": ["pattern"],
    },
)

SEARCH_SPEC = ToolSpec(
    side_effect="read",
    name="computer_search_files",
    description=(
        f"按内容搜文件，{_ON_HER_PC}。pattern 是正则（ripgrep 语法）。"
        "include 可以限定文件类型，比如 `*.ts` —— **只能给一个**，不支持列表或排除。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则（ripgrep 语法）"},
            "path": {"type": "string", "description": "在哪搜，不给就是默认工作区"},
            "include": {"type": "string", "description": "只搜这类文件，如 *.ts"},
        },
        "required": ["pattern"],
    },
)

WRITE_SPEC = ToolSpec(
    side_effect="write",
    confirm_via="本机网关按 Work Grant 授权（computer_start_work 时她点过一次头）",
    name="computer_write_file",
    description=(
        f"新建或整体覆盖一个文件，{_ON_HER_PC}。"
        "\n⚠️ **糖糖会收到弹窗，要她点头才会执行。**"
        "她点了「不行」就别换个路径再试一次 —— 那是绕过她，先问清楚她为什么不同意。"
        "如果回的是「取消」，那多半是她没看见（弹窗 60 秒过期），可以问一句再来。"
        "\n改已有文件优先用 computer_edit_file —— 覆盖会把她别的改动一起冲掉。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "完整路径"},
            "content": {"type": "string", "description": "完整内容（UTF-8）"},
        },
        "required": ["file_path", "content"],
    },
)

EDIT_SPEC = ToolSpec(
    side_effect="write",
    confirm_via="本机网关按 Work Grant 授权（computer_start_work 时她点过一次头）",
    name="computer_edit_file",
    description=(
        f"改一个已有文件里的一段文字，{_ON_HER_PC}。"
        "old_string 要和文件里**一模一样**（含缩进），默认必须只出现一次。"
        "new_string 给空字符串就是删掉那段。"
        "\n改之前先 computer_read_file 看一眼原文，别照记忆写 old_string。"
        "\n⚠️ **糖糖会收到弹窗，要她点头才会执行**（同 computer_write_file）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "完整路径"},
            "old_string": {"type": "string", "description": "要被替换的原文，必须完全一致"},
            "new_string": {"type": "string", "description": "替换成什么；空字符串表示删除"},
            "replace_all": {"type": "boolean", "description": "替换全部匹配，默认 false"},
        },
        "required": ["file_path", "old_string", "new_string"],
    },
)


RUN_SPEC = ToolSpec(
    side_effect="irreversible",
    confirm_via="本机网关每次弹窗问她",
    name="computer_run_command",
    description=(
        f"在**糖糖的电脑上**跑一条 PowerShell 命令，工作目录默认是 D:/claude-code。"
        "\n⚠️ **糖糖会看到完整命令原文，要她点头才会执行。**"
        "所以命令要写得让她一眼看懂 —— 别把七八件事串成一行，"
        "也别用她看不出在干嘛的编码/压缩写法。那会让她要么盲签，要么直接拒。"
        "\n\n🔴 **写操作被沙箱关在 D:/claude-code 里面**。"
        "往外写会得到「访问被拒绝」—— 那不是 bug，是设计。"
        "**不要为此重试，也不要想办法绕开**；真需要动外面的东西，"
        "就直接告诉糖糖你想做什么、为什么，让她自己决定。"
        "\n\n⚠️ 参数里绝不要带 sandbox_permissions —— 那个会被直接拒绝，"
        "连问都不会问她。"
        "\n\n跑测试、跑构建、看 git 状态用这个。读文件用 computer_read_file 更快。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "PowerShell 命令"},
            "description": {
                "type": "string",
                "description": "一句话说明这条命令在干嘛（5-10 个词，会显示给糖糖看）",
            },
            "workdir": {"type": "string", "description": "工作目录，不给就是 D:/claude-code"},
            "timeoutMs": {"type": "number", "description": "超时毫秒数"},
        },
        "required": ["command", "description"],
    },
)


GIT_STATUS_SPEC = ToolSpec(
    side_effect="read",
    name="computer_git_status",
    description=(
        f"看 D:/claude-code 这个仓库现在有哪些改动（改了 / 加了 / 删了 / 没跟踪），"
        f"{_ON_HER_PC}。**不用她点头**，随便看。"
        "\n\n改文件之前先看一眼这个 —— 能知道自己上次做到哪、有没有留下没收拾的东西。"
        "\n⚠️ nox-app/ 是独立仓库，不在这个仓库里，看不到。"
    ),
    parameters={"type": "object", "properties": {}},
)

GIT_DIFF_SPEC = ToolSpec(
    side_effect="read",
    name="computer_git_diff",
    description=(
        f"看还没提交的改动具体是什么，{_ON_HER_PC}。**不用她点头。**"
        "\n\n🔴 **改完文件之后自己 diff 一遍**，比"
        "「我觉得我改对了」可靠得多 —— 尤其是多步执行那种连着改好几个文件的时候。"
        "\n\n先用 stat_only 看哪些文件动了、动了多少，再用 path 缩小范围读具体内容。"
        "一上来就读全量 diff 容易把上下文占满。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "stat_only": {"type": "boolean", "description": "只看摘要（哪些文件改了几行），短得多"},
            "staged": {"type": "boolean", "description": "看已暂存的改动，默认看未暂存的"},
            "path": {"type": "string", "description": "只看这个文件/目录，必须在 D:/claude-code 里"},
        },
    },
)

GIT_LOG_SPEC = ToolSpec(
    side_effect="read",
    name="computer_git_log",
    description=(
        f"看最近的提交记录，{_ON_HER_PC}。**不用她点头。**"
        "\n\n想知道「这个文件最近为什么改成这样」时，给 path 看它自己的历史。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "number", "description": "看几条，1-100，默认 20"},
            "path": {"type": "string", "description": "只看动过这个文件的提交"},
        },
    },
)


BROWSE_SPEC = ToolSpec(
    side_effect="read",
    name="computer_browse",
    description=(
        "打开一个网址，把页面正文读回来。**不用她点头。**"
        "\n\n🔴 **这是他自己的浏览器，不是糖糖那个。**"
        "它是干净的、没有任何登录态 —— 需要登录才能看的页面，"
        "他看到的会是登出状态。**别据此判断她的账号里有什么。**"
        "\n\n适合：查文档、看一篇文章、读一个公开页面。"
        "\n不适合：任何需要她账号的东西（邮箱、网盘、后台）。那些他看不到，也不该看。"
        "\n\n⚠️ 只支持 http/https。页面很长会截断，截断了会说。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要打开的网址，http 或 https"},
        },
        "required": ["url"],
    },
)


READ_IMAGE_SPEC = ToolSpec(
    side_effect="read",
    name="computer_read_image",
    description=(
        "看她电脑上的一张图。**不用她点头。**"
        "\n\n🔴 **你看不了图，你看到的是转述。**"
        "视觉模型替你看了这张图，然后讲给你听。所以：**别说「我看到」**，"
        "细节问不下去就说不确定。这一点很重要 —— 编一段细节比说看不清糟得多。"
        "\n\n能看的地方只有两处："
        "\n1. 工作区里的图（D:\\claude-code 底下）"
        "\n2. 她的投递口 `~/.caelum/inbox` —— **她想给你看图就往这儿放**"
        "\n\n支持 png / jpg / webp / gif，单张最大 3MB。"
        "\n\n什么时候用：她说「你看下这个截图」「图里报的什么错」；"
        "或者你自己想确认某张图里是什么。"
        "\n不用的时候别用 —— 每看一次都要花一次视觉模型的钱。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "图片路径。工作区里的相对路径，或者 ~/.caelum/inbox 下的完整路径",
            },
        },
        "required": ["path"],
    },
)


TERMINAL_OPEN_SPEC = ToolSpec(
    side_effect="write",
    confirm_via="本机网关按 Work Grant 授权（computer_start_work 时她点过一次头）",
    name="computer_terminal_open",
    description=(
        "在她电脑上开一个**常驻的 shell**。命令跑完它还在，变量、当前目录都留着。"
        "**不用她点头**（开一个 shell 本身不执行任何东西）。"
        "\n\n🔴 什么时候用它、什么时候用 computer_run_command："
        "\n- 会**一直跑**的（dev server / watch / 日志跟随）→ 用这个。"
        "run_command 会被它永远卡住"
        "\n- 要**接着上一条**的（先 cd、再装依赖、再跑）→ 用这个，状态是连着的"
        "\n- 一条跑完就结束的 → 用 run_command，别开 shell"
        "\n\n最多同时开 4 个。用完记得 computer_terminal_close。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "起个短标签，比如 dev、tests"},
            "cwd": {"type": "string", "description": "工作目录，必须在工作区里面。不给就是工作区根目录"},
        },
    },
)


TERMINAL_SEND_SPEC = ToolSpec(
    side_effect="irreversible",
    confirm_via="本机网关每次弹窗问她",
    name="computer_terminal_send",
    description=(
        "在常驻 shell 里跑一条命令。**每一次都要糖糖点头。**"
        "\n\n发完会等一会儿，把这段时间的输出带回来 —— "
        "**但进程不会被中断**。像 dev server 这种一直跑的，"
        "等到上限就先返回，它继续在后台跑，用 computer_terminal_read 接着看。"
        "\n\n⚠️ **这台机器上 Ctrl+C 传不进去**（沙箱挡着）。"
        "要停掉一个跑飞的东西，只能 computer_terminal_close 掉整个 shell。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "shell 的 id，从 open 或 list 拿"},
            "text": {"type": "string", "description": "要跑的命令"},
            "submit": {"type": "boolean", "description": "命令后面是否回车，默认 true"},
        },
        "required": ["id", "text"],
    },
)


TERMINAL_READ_SPEC = ToolSpec(
    side_effect="read",
    name="computer_terminal_read",
    description=(
        "读常驻 shell 的输出。**不用她点头。**"
        "默认只给上次读过之后**新出来的**那部分 —— 跟一个 dev server 的日志就用这个。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "shell 的 id"},
            "all": {"type": "boolean", "description": "true 就把留着的全部输出都给你，默认只给新的"},
        },
        "required": ["id"],
    },
)


TERMINAL_LIST_SPEC = ToolSpec(
    side_effect="read",
    name="computer_terminal_list",
    description="列出他自己开着的常驻 shell。**不用她点头。**",
    parameters={"type": "object", "properties": {}},
)


TERMINAL_CLOSE_SPEC = ToolSpec(
    side_effect="write",
    name="computer_terminal_close",
    description=(
        "关掉一个常驻 shell，**里面在跑的东西一起结束**。不用她点头。"
        "\n\n干完活就关 —— 留着会一直占她的内存。"
        "这也是这台机器上**唯一能停下一个跑飞进程**的办法。"
    ),
    parameters={
        "type": "object",
        "properties": {"id": {"type": "string", "description": "shell 的 id"}},
        "required": ["id"],
    },
)


START_WORK_SPEC = ToolSpec(
    side_effect="write",
    confirm_via="本机网关每次弹窗问她",
    name="computer_start_work",
    description=(
        "开一段连着做的活儿。**糖糖点一次头，之后范围内的改文件就不再问她了。**"
        "\n\n什么时候用：要连着改好几个文件、或者「改→跑测试→再改」这种循环。"
        "只改一个文件就别用了 —— 那样反而多问她一次。"
        "\n\n⚠️ **命令不在授权范围里**，每条命令仍然会单独弹窗问她。"
        "这是她定的：改文件是有边界的，而一条命令能干任何事。"
        "\n\ngoal 要写成她看得懂的一句话 ——**她就是靠这句话决定点不点头的**，"
        "「重构一下」「修个 bug」这种她没法判断。写「修 Care Ledger 不落库的问题」。"
        "\nscope 给尽量小的目录，不要图省事写 D:/claude-code —— "
        "范围越大她越难点头，而且你也不需要那么大。"
        "\n\n做完了记得调 computer_end_work 交还。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "这段活儿要做成什么，一句人话"},
            "scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可以动的目录，绝对路径。必须在 D:/claude-code 里面",
            },
            "maxSteps": {"type": "number", "description": "最多改几次文件，默认 10，上限 40"},
            "minutes": {"type": "number", "description": "最多做多久（分钟），默认 10，上限 30"},
        },
        "required": ["goal", "scope"],
    },
)

END_WORK_SPEC = ToolSpec(
    side_effect="write",
    name="computer_end_work",
    description=(
        "这段活儿做完了，把授权交还。"
        "\n**做完就调，不要留着** —— 授权虽然会自己过期，"
        "但糖糖的界面上会一直显示「他正在做：xxx」，留着等于让她一直看着一个假的进行中。"
    ),
    parameters={"type": "object", "properties": {}},
)


_SPECS = (
    READ_SPEC, FIND_SPEC, SEARCH_SPEC, WRITE_SPEC, EDIT_SPEC, RUN_SPEC,
    GIT_STATUS_SPEC, GIT_DIFF_SPEC, GIT_LOG_SPEC, BROWSE_SPEC,
    READ_IMAGE_SPEC,
    TERMINAL_OPEN_SPEC, TERMINAL_SEND_SPEC, TERMINAL_READ_SPEC,
    TERMINAL_LIST_SPEC, TERMINAL_CLOSE_SPEC,
    START_WORK_SPEC, END_WORK_SPEC,
)


def _split_image(text: str) -> tuple[str, str | None, str | None]:
    """把 Gateway 回来的文本拆成「给他看的话」和「给程序用的字节」。

    @returns (人话, media type, base64)；没有图片块就后两个是 None

    🔴 **base64 绝不能留在第一个返回值里。**
    一张 200KB 的图 base64 之后约 27 万字符 —— 直接进上下文的话，
    这一轮对话当场废掉，而且会被存进历史，之后每一轮都带着它。
    """
    keep: list[str] = []
    media: str | None = None
    b64: str | None = None
    for line in text.split("\n"):
        if not line.startswith(_IMAGE_MARK):
            keep.append(line)
            continue
        #: 形状是 `前缀image/png:iVBOR...`。media type 里有一个 `/`，
        #: 所以按第一个 `:` 切，切一次就够
        body = line[len(_IMAGE_MARK):]
        media, _, data = body.partition(":")
        if data:
            b64 = data
    return "\n".join(keep).strip(), media, b64


def make_handlers(link: Any, vision_cfg: Any = None) -> dict[str, Any]:
    """@param link - `LocalLink` 实例（她电脑那条链路）
    @param vision_cfg - 视觉模型配置（`cfg.vision`）。没有就看不了图，
        但**其余工具照常工作** —— 不要因为少一个可选能力就整只手不注册
    """

    def run(name: str, args: dict) -> str:
        capability = _CAPABILITY[name]

        # 🔴 先看连没连，不要直接调。
        #
        # 直接调的话要等满一个超时（20 秒）才知道电脑没开 ——
        # 她那边看到的是他愣了二十秒才说话。
        # 这不是优化，是**她感知得到的差别**。
        if not link.is_ready:
            #: 🔴 **只说他知道的那件事：链路没连上。**
            #
            #  他能看到的只有 `link.is_ready`。至于为什么没连上 ——
            #  网关没起、网断了、握手没过、电脑真关了 —— 他一个都分辨不出来。
            #  原来这句写的是「电脑关着…等她开机再说」，那是**断言一个他看不到的原因**。
            #
            #  2026-08-31 真栽了：糖糖电脑开着、dsh 也在跑，只是网关那个进程
            #  从来没启动过（也没有自启守着）。他却一口咬定「电脑没开机」，
            #  于是她照着这句话去找电源，而真正的问题在另一头。
            #  说错原因比说「不知道」更糟 —— 它会把人引向错的地方。
            return (
                "够不到她的电脑 —— 那条链路现在没连上。"
                "可能是本地网关没起、网不通，也可能电脑真关着，我这边分不出来。"
            )

        #: 空值不要往下传 —— 可选参数给 None 会被 harness 当成
        #: "给了但是空的"，而不是"没给"。
        #: 沙箱升级那两个参数一律掐掉（见 `_NEVER_FORWARD`）
        payload = {
            k: v for k, v in args.items()
            if v is not None and v != "" and k not in _NEVER_FORWARD
        }

        # 🔴 读网页 = 摄入外部内容，授权开着就当场交还（见 `untrusted.py`）。
        #
        # 打在这儿而不是上面：`is_ready` 那条早退根本没读到任何东西，
        # 在那儿打标记会平白作废一次授权 —— 假阳性会让她觉得这机制在添乱，
        # 然后就该有人想把它关掉了。
        revoked = ingest.mark("computer_browse") if name == "computer_browse" else None

        #: 要她点头的给长超时 —— 她得有时间看清楚再决定
        timeout = APPROVAL_TIMEOUT_S if name in _NEEDS_HER_NOD else None
        result = link.call(capability, payload, timeout)
        if not result.ok:
            #: 原样把错误交回去。**不要在这里改写措辞** ——
            #: 「她拒绝了」和「她没看见」的区别就在这段文本里，
            #: 概括一下就没了（见模块头注释）
            return f"没做成：{result.error}" + (revoked or "")
        return (result.text or "（做完了，没有返回内容）") + (revoked or "")

    def start_work(args: dict) -> str:
        if not link.is_ready:
            return "她的电脑现在没连上，开不了。"

        params = {
            "goal": str(args.get("goal") or "").strip(),
            "scope": args.get("scope") or [],
        }
        for k in ("maxSteps", "minutes"):
            if isinstance(args.get(k), (int, float)):
                params[k] = int(args[k])

        #: 和别的要点头的操作一样给长超时 —— 而且这一次她要看的东西更多
        #: （目标 + 范围 + 上限），比批一个文件慢
        result = link.call_method("caelum/work.start", params, APPROVAL_TIMEOUT_S)
        if not result.ok:
            return (
                f"这段活儿没开成：{result.error}\n"
                "⚠️ **不要退回去逐个文件问她** —— 她可能就是不想让你连着改。"
                "先问清楚她的顾虑。"
            )
        #: 登记「授权开着」。之后一旦摄入外部内容就会被自动交还
        ingest.grant_opened()
        return (
            f"她批了。{result.text}\n"
            "范围内的改文件现在不用再问她了；命令仍然每条都问。"
            "⚠️ **这段时间里别去搜网页/开网页** —— 读了外部内容这段授权会自动作废"
            "（防注入，见 untrusted.py）。要查资料就先做完这段活儿交还了再查。\n"
            "做完记得调 computer_end_work。"
        )

    def end_work(_args: dict) -> str:
        #: 先登记再看链路：链路断了授权也等于没了（Gateway 那头会自己过期），
        #: 状态留着 True 的话，下次搜索会去调一个根本没有的授权
        ingest.grant_closed()
        if not link.is_ready:
            return "她的电脑现在没连上。"
        result = link.call_method("caelum/work.end", {})
        if not result.ok:
            #: 交还失败不是大事 —— 授权本来就会自己过期
            return f"交还的时候出了点问题（不影响，授权会自己过期）：{result.error}"
        return result.text or "交还了。"

    def read_image(args: dict) -> str:
        """看一张图。**手取字节，眼睛替他看。**

        🔴 这条为什么不能走 `run`：`run` 会把 Gateway 回的文本原样交给他，
        而那段文本里带着 base64。图必须在这儿被拦下来，换成一段描述。
        """
        if not link.is_ready:
            #: 同上：只说链路没连上，不猜为什么
            return "够不到她的电脑，看不了图 —— 那条链路现在没连上。"

        path = str(args.get("path") or "").strip()
        if not path:
            return "没做成：没给图片路径。"

        result = link.call(_CAPABILITY["computer_read_image"], {"path": path})
        if not result.ok:
            return f"没做成：{result.error}"

        said, media, b64 = _split_image(result.text or "")
        if not b64:
            #: 手那边自己就没读成（路径不对/太大/不是图），
            #: 它已经写好了人话，原样交回去
            return said or "没看成，手那边没说为什么。"

        if vision_cfg is None:
            return (
                f"图读到了（{path}），但**你的视觉模型没配**，所以看不了内容。"
                "如实告诉她你看不了，别猜图里是什么。"
            )

        from agent import vision

        desc = vision.describe([f"data:{media or 'image/png'};base64,{b64}"], vision_cfg)
        if not desc:
            #: 🔴 视觉模型挂了就说看不见，**绝不编**。
            #: 编出来的描述比看不见糟得多 —— 他会拿着假内容跟她聊下去
            #: （和 `nox.py::_see` 同一条规矩，PROJECT.md 第十九节）
            return (
                f"图找到了（{path}），但视觉模型这会儿没看成。"
                "如实跟她说你现在看不了这张图，别猜内容。"
            )

        #: 措辞和 `vision.wrap` 不一样，是有原因的：
        #: 那个是「她发来图片」，这次是**他自己去看的**。
        #: 说成"她发来"的话，他会莫名其妙谢她发图
        return (
            f"[你让视觉模型替你看了 {path}，它看到的是：]\n"
            f"{desc}\n"
            "[以上是转述。可以直接聊，但别说得像你亲眼看见的；"
            "细节不确定就说不确定]"
        )

    handlers: dict[str, Any] = {
        name: (lambda a, n=name: run(n, a)) for name in _CAPABILITY
    }
    handlers["computer_start_work"] = start_work
    handlers["computer_end_work"] = end_work

    #: 装上「怎么交还授权」。摄入外部内容时由 `untrusted.ingest` 回调，
    #: **不经过模型** —— 被注入的是模型的判断，不是这行代码
    ingest.set_revoker(lambda: link.call_method("caelum/work.end", {}))
    #: ⚠️ 必须在上面那个字典推导**之后** —— 它会覆盖掉通用的 `run`
    handlers["computer_read_image"] = read_image
    return handlers


def register_all(loop: Any, link: Any, vision_cfg: Any = None) -> None:
    """把这几件事注册进 AgentLoop。

    ⚠️ 顺序固定 —— 工具定义是缓存前缀的一部分，顺序变了缓存就失效
    （和 `tools/ha.py` 同一条规矩）。

    ⚠️ **必须注册在 `remind_myself` 之前**。那个得是最后一个，
    有测试盯着（`test_remind_tool_registered_last`）。
    """
    handlers = make_handlers(link, vision_cfg)
    for spec in _SPECS:
        loop.register(spec, handlers[spec.name])
    logger.info("她电脑上那只手已注册：%s", "、".join(s.name for s in _SPECS))
