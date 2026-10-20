# 三大联赛价值投注看板

英超、意甲、西甲的每日概率分析。GitHub Actions 每天 06:00 UTC（北京时间 14:00）自动运行，结果发布到 GitHub Pages。

本项目只做概率研究，不自动下单，也不构成投注建议。

## 流程

| 环节 | 用到的开源项目 | 做什么 |
| --- | --- | --- |
| 数据采集 | football-data.co.uk、[xgabora 镜像](https://github.com/xgabora/Club-Football-Match-Data-2000-2025)、[OddsHarvester](https://github.com/jordantete/OddsHarvester) | 近 5 季加本季赛果、约 40 家公司的平均和最高赔率；OddsPortal 未开赛的胜平负和大小球 2.5 赔率 |
| 预测建模 | [penaltyblog](https://github.com/martineastwood/penaltyblog) | 每个联赛单独拟合时间衰减的 Dixon-Coles 模型；自建 Elo（净胜球加权）经有序 logit 转为胜平负概率；两者 75/25 融合 |
| 赔率分析 | [goto_conversion](https://github.com/gotoConversion/goto_conversion) | 去除庄家抽水得到市场公平概率；优势 = 模型概率 × 赔率 − 1；0.25 倍凯利计算仓位 |
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
  value.py      价值投注筛选与凯利仓位
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
| The Odds API | Secret `THE_ODDS_API_KEY` | 在 the-odds-api.com 免费注册，每月 500 次额度，每天运行约用 6 次 |
| football-data.co.uk | 无需配置 | 每轮比赛前几天才发布赛程和赔率 |

Secret 在仓库 Settings → Secrets and variables → Actions → New repository secret 里添加。

## 回测结果（2024-07 至 2026-09，2261 场）

- 胜平负对数损失：融合模型 0.982，市场 0.967。市场更准。
- 按最优价下注：2500 注，ROI −0.05%；按市场均价：1998 注，ROI −3.0%。
- 结论：模型目前没有稳定跑赢市场，页面上的“价值投注”是模型和市场的分歧，应当作研究线索。

## 已知限制

- 升班马在本联赛不足 10 场时不建模。
- OddsPortal 会拒绝 GitHub Actions 的 IP（实测返回 HTTP 错误），需要代理或改用 The Odds API。
- football-data 的赛程赔率文件每周更新几次，周中可能为空。
