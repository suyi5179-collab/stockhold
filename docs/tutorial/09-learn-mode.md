# 09 教学模式：页面上的每一步都是后端的真实状态

> 视频：[p09-learn.mp4](../videos/p09-learn.mp4) · tag：`step-09`

## 这一章解决什么问题？

前八章做出来的东西，散在 DynamoDB、两个队列、四个 Lambda、S3 和 DuckDB 里。读代码的人很难在脑子里把一单从头串到尾。
教学模式（`/learn`）做一件事：真的下一单，把它走到了哪一步、每一步留下了什么数据，摆在一个页面上。

## 为什么不能做成动画？

动画只证明前端会画图。页面上的每个阶段都是轮询 `GET /learn/api/runs/<run_id>` 得到的，后端每次都从真实存储里重新推导（`stockhold/learn.py`，锚点 `learn-trace`）：

| 阶段 | 从哪里读 |
|---|---|
| 提交样例订单 | 这次运行记下的请求和响应 |
| reserve | hold 行里的 `reserve`：分片、available before→after、version before→after、冲突次数；再读一次分片的当前行 |
| 延迟消息入队 | hold 的 `expiry_message`：消息 id、延迟秒数、消息体 |
| 付款失败 / 到期 | 付款事件的消息 id；或者离 expiresAt 还剩几秒 |
| release / expire Lambda | hold 的 history 里 from→to 和由谁完成；Lambda 写到 CloudWatch Logs 里带这个 hold id 的那几行 |
| 事件写进 S3 | 在当天的分区里找到包含这个 hold 的 JSON 行 |
| DuckDB 查询结果 | 镜像事件日志，跑 `rates_by_product.sql` 和 `hold_timeout_recommendation.sql` |

「坏消息样例」多一条支线：故障开关 → 三次失败记录 → 死信归档 → 重放工具里的 dry-run 审计 → 重放审计 → hold 真的变成 RELEASED。dry-run 和重放要你自己去 `/ops` 点（登录 `oncall / oncall-demo`），页面只负责看后端什么时候记下了它们。

启动一单的代码在锚点 `learn-start`：和 API 走同一个 `inventory.reserve` 和 `expiry.schedule`，付款失败的 webhook 四秒后才发，像真实的拒付。

## 「看源码」链接怎么保证不过期？

源码里用成对的注释标出关键位置：开始一行 `@learn:<id>`，结束一行 `@learn:end <id>`，两行都在范围外面。
`scripts/learn_anchors.mjs` 扫出 `{id: {path, start, end}}`，写进 `stockhold/static/generated/anchors.json`，行号从来不手写。
链接是 `<REPO_URL>/blob/<GIT_SHA>/<path>#L<start>-L<end>`（锚点 `source-url`）：`REPO_URL` 只在 `stockhold/config.py` 定义一次，`GIT_SHA` 默认取 `git rev-parse HEAD`，所以链接固定在当前 commit 上，不会因为 main 往前走而指错行。

`scripts/check_anchors.mjs`（锚点 `anchor-refs`）在 CI 里检查四件事：生成文件没过期；页面、文档、视频脚本引用的每个 id 都存在；每个范围在文件里、紧挨着它的标记；每个锚点都被引用过。

## 真实跑一次

Playwright 打开 `/learn`，点「提交样例」，记录阶段变绿的顺序：

```
log [ 'order', 'reserve', 'enqueue', 'trigger', 'lambda', 's3', 'duckdb' ]
```

reserve 这一步显示的是真实的那一行：`available 25000 → 24998`、`version 0 → 1`、条件 `available >= :n AND version = :v`、尝试 / 冲突 `1 / 0`。

## 面试题和日常问题两个标签页

`/learn/interview` 渲染 [docs/interview.md](../interview.md)，`/learn/problems` 渲染 [docs/problems.md](../problems.md)：每题折叠，点开看答案；文中写的锚点名和相对路径都换成固定 commit 的 GitHub 链接。
每个阶段和每个问题旁边有「看讲解视频」：`/videos/<part>.mp4`、`/videos/problems/<id>.mp4`，由 Flask 从 `docs/videos/` 提供；文件还没生成时显示「视频待生成」。

## 录屏脚本

`video/record.mjs` 用 Playwright 的 chromium 按 `video/narration.json` 一段一段地录：执行动作 → 用红框套住要讲的那个值（锚点 `record-frame`）→ 停留这一段的时长（`video/durations.json`，没有就每段四秒，锚点 `record-pacing`）。输出一个 webm 和 `video/timeline.json`（每段的实际开始毫秒和 part），主会话按 part 切片配音。
`video/check_frames.mjs` 检查每个红框都套住了目标、没有框偏、没有框整个面板。

## 怎么 checkout 到这一步跑起来

```bash
git checkout step-09
./scripts/demo.sh                     # http://127.0.0.1:8840/learn
node scripts/check_anchors.mjs
cd e2e && npx playwright test
```
