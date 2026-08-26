# Caelum-OS Fork 分支策略

> 2026-08-16 定。阶段 2 fork 的维护流程，写下来省得每次升级都现想。

## 现状

- Caelum-OS `main` = deepseek-harness 官方 master（HEAD `47f9438`，12293 commit，2026-08-16 fork）
- 本地 `D:\deepseek-harness`：`origin` = 官方 deepseek-ai/deepseek-harness，`caelum` = Caelum-OS
- 我们的东西全在 `caelum-os/` 目录（补丁 + 重放脚本 + 文档），随仓库一起管理

## 分支策略

- **main**：官方代码追踪 + `caelum-os/` 目录（我们的层）。**不改官方代码**（内核最小脚印）。
- **官方升级**：从 `origin` fetch → merge 到本地 master → push 到 `caelum` main
- **我们的内核改动**：不直接 commit 进官方代码文件，而是生成 patch 放
  `caelum-os/patches/`，用 `apply-patches.ps1` 重放

## 官方升级流程（一次完整升级）

```bash
cd D:\deepseek-harness
git fetch origin                                # 拉官方最新
git merge origin/master                         # 合并官方（caelum-os/ 目录官方没有，不会冲突）
.\caelum-os\apply-patches.ps1                   # 把我们的内核补丁铺回去
# 若补丁因上下文变化 apply 失败 → 手动解决，重新生成 patch（见下）
git add caelum-os\patches\
git commit -m "sync upstream + re-apply caelum patches"
git push caelum master:main
```

## 新增内核补丁的流程

1. 改代码（`D:\deepseek-harness` 里的官方文件）
2. `git diff -- <改动文件> > caelum-os\patches\NNNN-描述.patch`
3. 跑 `.\caelum-os\apply-patches.ps1` 验证
4. commit patch 文件（**不 commit** 官方文件的改动）

## 原则

- **内核最小脚印**：能放 `caelum-os/` 的东西，不改官方代码
- **补丁是唯一真源**：我们的内核改动以 patch 为准，官方代码文件保持干净
- **每次升级后立刻跑重放脚本**，别攒（攒多了冲突一起爆，难排查）
