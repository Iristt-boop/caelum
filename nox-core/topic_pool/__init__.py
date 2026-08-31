"""Topic Pool —— 话题池。

《Topic_Pool_实事话题池链路.md》的落地：Scout 每 6 小时抓一轮外部候选，
utility 模型筛出 0~3 条真正值得带回来的 Topic；共读/共听/共影的未完成
枝条从 World Model 直接投影进来（origin=shared，不过网络）。最后进同一个
池子：**最近有什么东西，值得我们再看一眼。**

边界（文档 §4.3，写死）：follow 是请求，不是自动任务。池子只负责
**发现**值得看的东西；要研究、要聊，都是 Nox 和糖糖自己的决定。
"""

from topic_pool.pool import TopicPool, run_topic_loop
from topic_pool.store import Topic, TopicStore

__all__ = ["Topic", "TopicPool", "TopicStore", "run_topic_loop"]
