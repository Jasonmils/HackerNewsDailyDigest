# HN Daily Digest Agent

每天从 Hacker News 抓取热榜，用 DeepSeek 为每条生成结构化中文（或英文）摘要，输出一份可读的 Markdown + HTML 日报。

```
topstories ──► 抓元数据 ──► 抓正文 + 热门评论 ──► DeepSeek 摘要 ──► Markdown + HTML
                (并发)        (并发)             (并发, 默认 deepseek-v4-pro)
```

每条产出：一句话概要 · 3–5 条要点 · 评论区观点/分歧 · 主题标签。

---

## 安装

```bash
pip install -r requirements.txt
export DEEPSEEK_API_KEY=sk-...        # 或直接在 hn_digest.py 顶部的 DEEPSEEK_API_KEY 处填入
```

> DeepSeek 接口与 OpenAI 兼容，脚本通过 `openai` SDK 指向 `https://api.deepseek.com` 调用。API key 可填在 `hn_digest.py` 顶部的 `DEEPSEEK_API_KEY = ""`（默认留空），或用上面的环境变量（环境变量优先）。

## 运行

```bash
python hn_digest.py                              # 热榜前 10，中文摘要，输出到 ./digests/
python hn_digest.py --num 20 --lang en           # 前 20，英文摘要
python hn_digest.py --keywords AI,LLM,crypto,Rust # 只保留标题命中关键词的故事（个性化日报）
python hn_digest.py --proxy http://127.0.0.1:7897 # API 与正文抓取都走本地代理
python hn_digest.py --no-articles                # 只用标题 + 评论（更快、更省）
```

输出文件：`digests/hn-digest-YYYY-MM-DD.md` 和 `.html`。HTML 是自包含单文件，可直接浏览器打开或作为邮件正文。

## 常用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--num N` | 10 | 摘要的故事数 |
| `--model ID` | `deepseek-v4-pro` | 换 `deepseek-v4-flash` 更快更省，质量略低 |
| `--lang zh\|en` | `zh` | 摘要语言（标签/版式同步切换）|
| `--keywords a,b,c` | 无 | 按标题关键词过滤；命中前会先抓 `--pool`（默认 200）条做筛选 |
| `--concurrency N` | 6 | 并发摘要槽位（抓正文 + 调模型）|
| `--max-comments N` | 8 | 喂给模型的热门评论条数 |
| `--proxy URL` | `$HTTPS_PROXY` | 代理；也可用环境变量 `HN_DIGEST_PROXY` |
| `--no-cache` | — | 强制重新摘要（默认按故事 id 缓存，避免重复付费）|
| `--no-thinking` | — | 关闭 DeepSeek-V4 思考模式（默认开启）|
| `--reasoning-effort high\|max` | `high` | 思考模式的推理强度，仅在思考模式开启时生效 |
| `--judge` | — | 判断力模式（交互式）：先藏讨论逼你预测，再揭晓 + 最强反驳，写入台账 |
| `--horizon N` | 30 | 判断力模式：新预测多少天后到期待打分 |
| `--grade-only` | — | 判断力模式：只把到期的旧预测捞出来打分，然后退出 |

## 🧠 判断力模式（calibration）

为「读日报来练判断力」设计的主动训练回路。把 `--judge` 加到任意命令上即进入**交互式**流程：

```bash
python hn_digest.py --judge                       # 前 10 条，逐条训练
python hn_digest.py --judge --num 5 --horizon 60  # 5 条，预测 60 天后到期
python hn_digest.py --grade-only                  # 只复盘到期旧预测，不抓新日报
```

一次 `--judge` 的流程：

1. **打分到期预测**：开跑先从台账 `digests/ledger.json` 捞出 `到期日 ≤ 今天` 的旧预测，
   逐条让你自评 命中(h)/未中(m)/部分(p) + 复盘备注，并算出累计 **Brier 分**（越低越准）。
2. **藏讨论、逼你预测**：每条故事只显示标题/翻译/概要/要点/标签，外加模型生成的一个
   **可证伪、有时限**的预测问题；评论讨论与反驳全部隐藏。你在终端写下预测 + 置信度(0–100)。
3. **揭晓 + 最强反驳**：回车后才揭晓评论区讨论、回复最多的评论，以及模型甩出的**最强一条反驳**
   （steelman）。
4. **写进台账**：预测 + 置信度 + 到期日（今天 + `--horizon`）落到 `digests/ledger.json`，
   下次跑时到期就会被捞出来打分。

> 判断力模式是交互式的，**不适合挂 cron**；它仍会照常生成当天的 Markdown/HTML 日报（含判断力区块）。
> 台账是一个普通 JSON 文件，可自行查看/编辑（比如手动改 `resolve_by` 让某条预测立刻到期）。
> 打分纯靠你自评——模型无法预知未来，Brier 分只反映你自己的校准程度。

## 定时（每天早上 8 点）

```cron
0 8 * * * cd /path/to/hn-agent && DEEPSEEK_API_KEY=sk-... \
  /usr/bin/python3 hn_digest.py --num 30 >> digests/run.log 2>&1
```

## 发布成每日更新的网页（GitHub Pages）

仓库已内置一套零运维方案：`.github/workflows/daily-digest.yml` 让 GitHub Actions 每天云端生成 digest，并自动部署到 GitHub Pages，**无需本地开机**。

一次性设置：

1. **先确认 `hn_digest.py` 顶部的 `DEEPSEEK_API_KEY` 是空字符串**（已清空），key 只通过 secret 注入——千万别把 key 写回代码再推到公开仓库。
2. 把项目推到一个 GitHub 仓库（公开仓库免费用 Pages；私有仓库需 Pro）。
3. 仓库 → **Settings → Secrets and variables → Actions → New repository secret**：名称填 `DEEPSEEK_API_KEY`，值填你的 key。
4. 仓库 → **Settings → Pages → Source** 选 **GitHub Actions**。
5. 仓库 → **Actions → Daily HN Digest → Run workflow** 手动跑一次验证。完成后访问 `https://<用户名>.github.io/<仓库名>/` 即是入口页。

之后每天 **08:00（北京时间）** 自动：生成当天 digest → 提交回仓库（存档 + 缓存持久化）→ 重建入口页 → 部署到 Pages。

- 改时间：编辑 workflow 里的 `cron`（用 **UTC**，`0 0 * * *` = 北京 08:00）。
- 改每天条数：同文件里的 `--num 30`。

> 入口页由 `build_index.py` 生成（扫描 `digests/hn-digest-*.html`，最新置顶 + 往期列表）。本地预览：`python build_index.py` 会在 `./public/` 生成完整站点。
> 私有的 `digests/ledger.json`（判断力台账）和 `.cache` 不会出现在公开站点里（见 `.gitignore` 与 `build_index.py`）。

## 成本

默认 `deepseek-v4-pro`（约 $0.435 / $0.87 每百万 tokens，cache-miss 输入价），价格远低于主流闭源模型，30 条全量摘要单次成本通常在几分钱到一两毛量级；长期跑会因为热榜常驻故事命中缓存而更低。换 `deepseek-v4-flash`（约 $0.14 / $0.28）更快更省。脚本结束时会按 `PRICING` 表给出 token 用量与粗略成本估算，DeepSeek 调价时记得更新该表。

> 注：旧模型名 `deepseek-chat` / `deepseek-reasoner` 将于 2026-07-24 弃用（届时映射到 V4-Flash 的非思考 / 思考模式），脚本仍保留它们的价格条目以兼容。

## 工作方式

- **HN API**：无需鉴权的 Firebase 接口。`topstories.json` 取排名，`item/{id}.json` 取每条详情、`kids` 取评论。带 3 次重试。
- **正文抽取**：`httpx` 拉 HTML，`trafilatura` 提取正文（自动跳过 PDF/视频等非 HTML；Ask/Show HN 直接用帖子自带 `text`）。正文超长按字符截断。
- **摘要**：每条一次模型调用，要求只返回 JSON（`summary` / `key_points` / `discussion` / `tags`），解析容错（去 ``` 包裹、兜底正则提取）。token 用量累加并在结束时给出估算成本。
- **缓存**：`digests/.cache/{id}.json` 按故事 id 缓存摘要——常驻热榜的故事跨天不重复付费。注意评论是基于首次摘要时的快照；要刷新加 `--no-cache`。

## 可扩展方向

- 接入邮件：把 `hn-digest-*.html` 作为正文发给自己（SMTP 或任意邮件 API）。
- 更"代理化"：把"判断哪些故事值得深挖"交给模型做一次筛选/打分，而非纯关键词；或加 `tool_use` 让模型按需追加抓取。
- 换数据源：`topstories` 换成 `beststories`（高赞）或 `newstories`（最新）即可，逻辑不变。
