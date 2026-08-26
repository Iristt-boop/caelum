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
})


#: 工具名 → 链路上的能力名。**唯一一份**，加能力时只改这里。
_CAPABILITY: dict[str, str] = {
    "computer_read_file": "computer.read_file",
    "computer_find_files": "computer.find_files",
    "computer_search_files": "computer.search_files",
    "computer_write_file": "computer.write_file",
    "computer_edit_file": "computer.edit_file",
    "computer_run_command": "computer.run_command",
}


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


START_WORK_SPEC = ToolSpec(
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
    START_WORK_SPEC, END_WORK_SPEC,
)


def make_handlers(link: Any) -> dict[str, Any]:
    """@param link - `LocalLink` 实例（她电脑那条链路）"""

    def run(name: str, args: dict) -> str:
        capability = _CAPABILITY[name]

        # 🔴 先看连没连，不要直接调。
        #
        # 直接调的话要等满一个超时（20 秒）才知道电脑没开 ——
        # 她那边看到的是他愣了二十秒才说话。
        # 这不是优化，是**她感知得到的差别**。
        if not link.is_ready:
            return "她的电脑现在没连上（Caelum OS 没开，或者电脑关着）。这件事等她开机再说。"

        #: 空值不要往下传 —— 可选参数给 None 会被 harness 当成
        #: "给了但是空的"，而不是"没给"。
        #: 沙箱升级那两个参数一律掐掉（见 `_NEVER_FORWARD`）
        payload = {
            k: v for k, v in args.items()
            if v is not None and v != "" and k not in _NEVER_FORWARD
        }

        #: 要她点头的给长超时 —— 她得有时间看清楚再决定
        timeout = APPROVAL_TIMEOUT_S if name in _NEEDS_HER_NOD else None
        result = link.call(capability, payload, timeout)
        if not result.ok:
            #: 原样把错误交回去。**不要在这里改写措辞** ——
            #: 「她拒绝了」和「她没看见」的区别就在这段文本里，
            #: 概括一下就没了（见模块头注释）
            return f"没做成：{result.error}"
        return result.text or "（做完了，没有返回内容）"

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
        return (
            f"她批了。{result.text}\n"
            "范围内的改文件现在不用再问她了；命令仍然每条都问。"
            "做完记得调 computer_end_work。"
        )

    def end_work(_args: dict) -> str:
        if not link.is_ready:
            return "她的电脑现在没连上。"
        result = link.call_method("caelum/work.end", {})
        if not result.ok:
            #: 交还失败不是大事 —— 授权本来就会自己过期
            return f"交还的时候出了点问题（不影响，授权会自己过期）：{result.error}"
        return result.text or "交还了。"

    handlers: dict[str, Any] = {
        name: (lambda a, n=name: run(n, a)) for name in _CAPABILITY
    }
    handlers["computer_start_work"] = start_work
    handlers["computer_end_work"] = end_work
    return handlers


def register_all(loop: Any, link: Any) -> None:
    """把这几件事注册进 AgentLoop。

    ⚠️ 顺序固定 —— 工具定义是缓存前缀的一部分，顺序变了缓存就失效
    （和 `tools/ha.py` 同一条规矩）。

    ⚠️ **必须注册在 `remind_myself` 之前**。那个得是最后一个，
    有测试盯着（`test_remind_tool_registered_last`）。
    """
    handlers = make_handlers(link)
    for spec in _SPECS:
        loop.register(spec, handlers[spec.name])
    logger.info("她电脑上那只手已注册：%s", "、".join(s.name for s in _SPECS))
