# 三大联赛与欧冠价值投注看板

英超、意甲、西甲的每日概率分析，另设欧冠板块。GitHub Actions 每天 03:00 UTC（北京时间 11:00）自动运行：先复盘前一天的赛果，再出当天推荐，16:00 前完成，结果发布到 GitHub Pages。

本项目只做概率研究，不自动下单，也不构成投注建议。

## 流程

| 环节 | 用到的开源项目 | 做什么 |
| --- | --- | --- |
| 数据采集 | football-data.co.uk、[xgabora 镜像](https://github.com/xgabora/Club-Football-Match-Data-2000-2025)、[OddsHarvester](https://github.com/jordantete/OddsHarvester) | 近 5 季加本季赛果、约 40 家公司的平均和最高赔率；OddsPortal 未开赛的胜平负和大小球 2.5 赔率 |
| 预测建模 | [penaltyblog](https://github.com/martineastwood/penaltyblog) | 每个联赛单独拟合时间衰减的 Dixon-Coles 模型；自建 Elo（净胜球加权）经有序 logit 转为胜平负概率；两者 75/25 融合 |
| 赔率分析 | [goto_conversion](https://github.com/gotoConversion/goto_conversion) | 去除庄家抽水得到市场公平概率；优势 = 模型概率 × 赔率 − 1；0.25 倍凯利计算仓位 |
| 欧冠 | [openfootball/champions-league](https://github.com/openfootball/champions-league)、The Odds API | 三大联赛与近 5 季欧冠比赛放进同一套跨联赛 Elo，再用欧冠交手结果拟合各国联赛强度修正；胜平负取有序 logit 与 Elo-泊松各半，大小球来自 Elo-泊松。未经回测 |
| 回测 | 自写（思路参考 [sports-betting](https://github.com/georgedouzas/sports-betting)） | 从两季前开始，每周一用此前数据重新拟合，只预测当周，按市场均价和最优价分别结算 |

## 目录

```
footy/
  config.py     联赛、模型和下注规则参数（改这里）
  data.py       历史赛果与 football-data 赛程赔率
  odds.py       OddsHarvester 抓取、解析、去水、多来源合并
  odds_api.py   The Odds API 赔率
  teams.py      两个数据源之间的队名统一
  model.py      Dixon-Coles、Elo、融合
  ucl.py        欧冠赛果、跨联赛 Elo 与概率
  zucai.py      传统足彩 14 场 / 任九：500 彩票网期次、出票规则、历史模拟
  value.py      价值投注、高赔与冷门筛选，凯利仓位
  backtest.py   滚动回测
  predict.py    未来比赛预测
  report.py     生成网页
templates/report.html.j2
tests/
.github/workflows/daily.yml
```

## 本地运行

```bash
pip install -r requirements.txt
python -m playwright install chromium   # OddsHarvester 需要
python -m footy                          # 生成 site/index.html
python -m footy --no-live-odds           # 不抓 OddsPortal，只用 football-data 赔率
python -m pytest -q
```

## 部署

1. 仓库 Settings → Pages → Build and deployment → Source 选 **GitHub Actions**
2. Actions 页面手动运行一次 `daily-report`，之后每天自动更新

## 实时赔率来源（按优先级）

| 来源 | 配置 | 说明 |
| --- | --- | --- |
| OddsPortal（OddsHarvester） | 可选 Secret `ODDSPORTAL_PROXY_URL` | OddsPortal 会拒绝 GitHub 机房 IP，不配代理基本抓不到 |
| The Odds API | Secret `THE_ODDS_API_KEY` | 在 the-odds-api.com 免费注册，每月 500 次额度，每天运行约用 8 次（含欧冠）；欧冠赔率只来自这里 |
| football-data.co.uk | 无需配置 | 每轮比赛前几天才发布赛程和赔率 |

Secret 在仓库 Settings → Secrets and variables → Actions → New repository secret 里添加。

## 回测结果（2024-07 至 2026-09，2261 场）

- 胜平负对数损失：融合模型 0.982，市场 0.967。市场更准。
- 按最优价下注：2500 注，ROI −0.05%；按市场均价：1998 注，ROI −3.0%。
- 结论：模型目前没有稳定跑赢市场，页面上的“价值投注”是模型和市场的分歧，应当作研究线索。

## 高赔与冷门

页面另列一张胜平负“高赔与冷门”表：市场热门胜率 ≥ 60% 而模型给平局或弱队胜的概率比市场高 3 个百分点以上的标“冷门”；赔率 3–10 且模型优势 ≥ 5% 的标“高赔”。仓位为 1/8 凯利，单注上限 1%，并附模型认为该结果下最可能的比分。三大联赛回测里这类选项整体亏损（按均价 ROI 约 −10%），只适合作为爆冷线索。参数在 `config.py` 的 `LONG_*` / `UPSET_*`。

## 传统足彩（14 场 / 任九）

每期对阵从 500 彩票网（trade.500.com/sfc/）抓取，体彩官网拒绝境外访问。三大联赛和欧冠场次与赛程保持一致（同一套模型概率，赛程有胜平负价值推荐的足彩也选它；冷门/高赔推荐不强制），其他联赛用 500 网的平均概率并略向基准率修正。每期一套：14 场最多 2 场双选（4 注 8 元），任九选最稳的 9 场、最多 1 场双选（2 注 4 元），双选优先补模型认为被低估的冷门。抓不到期次时用我们自己的 14 场比赛生成“模拟期次”（无法购买）。参数在 `config.py` 的 `ZC_*`。

## 复盘与每周自我学习

`footy/journal.py` 每次运行把每场未开球比赛的最新预测存进 `journal/predictions.json`，足彩选号存进 `journal/lottery.json`，工作流把 `journal/` 提交回 main（提交信息带 `[skip ci]`，不会再触发运行）。第二天先结算：三大联赛用 football-data，仍未结算的用 The Odds API 比分接口（每个赛事 2 credits，只在有待结算比赛时调用），足彩用 500 网期次页上的比分。复盘统计“我们的判断”与市场热门的命中、各类推荐按当时最优赔率的盈亏，以及每场爆冷（非热门、市场概率 < 30%）当时市场和模型给了多少、有没有提前提示。

`footy/learn.py` 每周一在复盘后重新挑选几项规则参数（Elo 权重、向市场靠拢比例、价值/冷门/高赔门槛、足彩双选门槛）：在较早的回测比赛上挑出最好的取值，只有在最近 365 天上也明显更好才采用，结果写进 `journal/learned.json`，每次检查（采用与否）都显示在页面上。删除该文件即恢复 `config.py` 的初始值。

## 推荐理由（场外因素）

`footy/context.py` 给 48 小时内开球、有推荐的比赛补充场外因素：近况、休息天数和下一场比赛、积分形势与战意、上一场阵型和首发（ESPN 公开数据），开球时天气（Open-Meteo），以及伤停名单（API-Football，可选 secret `API_FOOTBALL_KEY`；免费版每天 100 次，代码限 90 次，且只能查昨天到明天的日期，所以阵型改用 ESPN），伤停球员与上一场首发对上就算主力缺阵。每条推荐按规则标出哪些因素支持、哪些削弱，并对比上次快照的市场概率变化。这些因素只展示、不改概率，写入存档供日后检验。

## 已知限制

- 升班马在本联赛不足 10 场时不建模。
- 欧冠：免费数据没有历史欧冠赔率，板块未经回测；球队评分比赛不足 6 场时不建模。openfootball 还没有本季欧冠赛果时，三大联赛以外球队的评分停留在上季末（发布后自动纳入）。
- OddsPortal 会拒绝 GitHub Actions 的 IP（实测返回 HTTP 错误），需要代理或改用 The Odds API。
- football-data 的赛程赔率文件每周更新几次，周中可能为空。

## 每天两次更新与周报

- **11:00（am）**：复盘前一天，再按模型出推荐；场外因素只展示，同时把它们对应的概率调整量记入存档。
- **19:00（pm）**：重新拉赔率、伤停、天气，按 `footy/adjust.py` 的规则小幅调整概率（疲劳 2、轮换 1.5、战意 1.5、每多一名首发缺阵 1 个百分点，最多 3 名；坏天气让大 2.5 降 2 个百分点），乘以 `CTX_WEIGHT` 后重算推荐和足彩（只更新未停售的期次）。推荐可以和上午不同，卡片上会标出调整和变化。
- 存档 `journal/predictions.json` 每场保留最新的 am 和 pm 两版（`versions`），复盘以开球前最后一版结算，并对比两版成绩。
- **每周一 11:00**：页面“本周复盘”汇总上一周（周一至周日）的命中、盈亏、爆冷，以及 11:00 与 19:00 两版对比；自我学习同时重新校准 `CTX_WEIGHT`（存档里至少 40 场带调整的已结束比赛才会动，没帮助会降到 0）。
- GitHub 的定时任务常推迟几个小时，所以每个时段有三个触发时间（am：北京时间 07:17、09:17、10:43；pm：16:17、18:17、18:43）。最先开始的那次干活并写入 `journal/runs.json`，同一天同一时段之后的触发直接跳过。
- 手动运行可在 Actions 里选 `session`：`am` 或 `pm`，留空按北京时间（15 点后为 pm）。

