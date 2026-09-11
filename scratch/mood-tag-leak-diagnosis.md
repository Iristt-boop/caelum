# [mood:xxx] 漏显根因（2026-09-06 排查完，修复未做——另一会话在部署，本会话已停手）

## 现象
糖糖看到小克的回复里出现裸文字 [Mood:开心]（她以为表情包坏了）。

## 根因：剥除链路大小写敏感，模型偶尔写大写 [Mood:xxx]
生产库实锤，历史至少漏过 3 次，全是大写 M：
- 08-20 content 以 [Mood: 平静] 结尾（segments 里也有）
- 08-21 以 [Mood:平静] 结尾
- 08-26 整条消息只有 [Mood: 撒娇]

两处漏点：
1. agent/llm.py MoodTagFilter._PREFIX = "[mood:"（~167 行）——流式过滤器
   startswith 只认小写，[Mood: 直接放行上屏
2. personality/mood.py _MOOD_TAG = re.compile(r"\[mood:\s*([^\]]{1,12})\]\s*$",
   MULTILINE)（~52 行）——没收 IGNORECASE，extract() 不剥、他的情绪状态也不更新

## 修法（都很小）
1. llm.py MoodTagFilter：比较时用 buf.lower()（放行时要吐原始 buf，别吐小写）
2. mood.py 正则加 re.IGNORECASE
3. 回归测试：tests/test_stream.py、tests/test_mood.py 按现有用例风格加
   大写变体（如 "[Mood:平静]" 分片到达也要挡）

## 备注
- 表情包系统本身没坏：send_meme 工具链路正常，糖糖实测已收到表情
- 症状间歇性（两周 3 次），不紧急但值得修
- 本会话在 nox-app 仓库有已提交改动（她发表情 2f244c8）、root 仓库 23ea5a3，
  均已上线完成，与本 mood 问题无关
