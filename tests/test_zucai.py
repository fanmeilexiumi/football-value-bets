from datetime import date

from footy import zucai

ROW = ('<tr class="bet-tb-tr" data-isend="0" data-cid="{n}" data-vs="x" data-pjgl="{pj}">'
       '<td class="td td-no">{n}</td><td class="td td-evt"><a href="#" style="">{comp}</a></td>'
       '<td class="td td-endtime">10-21 03:00</td><td class="td td-team"><div class="team"> <span class="team-l">'
       '<i>[5]</i><a href="#" class="team-l">{h}</a></span> <i class="team-vs">VS</i> <span class="team-r">'
       '<a href="#" class="team-r">{a}</a><i>[14]</i></span> </div></td></tr>')


def _page(rows):
    head = '<li class="chked" data-expect="26146" ><i class="ico-chk"></i>当前第26146期</li><span class="zcfilter-endtime">官方售彩截止时间：10-20 22:00</span>'
    return head + "".join(ROW.format(n=i + 1, **r) for i, r in enumerate(rows))


def _rows():
    rows = [dict(comp="欧冠", h="利物浦", a="比利亚雷亚尔", pj="63.75,20.20,16.05"),
            dict(comp="欧冠", h="日尔曼", a="巴萨", pj="39.14,21.88,38.98")]
    rows += [dict(comp="德甲", h=f"主{i}", a=f"客{i}", pj=f"{40 + 3 * i},25,{35 - 3 * i}") for i in range(12)]
    return rows


def test_parse_period():
    p = zucai.parse_period(_page(_rows()), date(2026, 10, 20))
    assert p["expect"] == "26146" and p["deadline"] == "10-20 22:00" and len(p["games"]) == 14
    g = p["games"][0]
    assert (g["comp"], g["kickoff"], g["home_cn"], g["away_cn"]) == ("欧冠", "2026-10-21 03:00", "利物浦", "比利亚雷亚尔")
    assert abs(sum(g["market"]) - 1) < 1e-9


def test_attach_and_plan_respect_budget():
    p = zucai.parse_period(_page(_rows()), date(2026, 10, 20))
    preds = [
        {"league": "CL", "bj_date": "2026-10-21", "bj_time": "03:00", "home": "Liverpool", "away": "Villarreal",
         "model": {"H": 0.6, "D": 0.2, "A": 0.2}, "market": {},
         "longshots": [{"selection": "D", "edge": 0.3}],
         "bets": [{"selection": "A", "edge": 0.2}, {"selection": "O25", "edge": 0.1}]},
        {"league": "CL", "bj_date": "2026-10-21", "bj_time": "03:00", "home": "Paris SG", "away": "Barcelona",
         "model": {"H": 0.3, "D": 0.35, "A": 0.35}, "market": {}},
    ]
    out = zucai.build([p], preds)[0]
    assert out["games"][0]["home"] == "Liverpool" and out["games"][1]["home"] == "Paris SG"
    assert out["games"][2]["model"] is None and out["games"][2]["source"] == "市场"
    # the schedule has an away value bet at Liverpool: the 14-match ticket must include it
    liv = out["games"][0]["t14"]
    assert liv["fixed"] and "0" in liv["picks"]
    assert out["games"][0]["r9"] is None or "0" in out["games"][0]["r9"]["picks"]
    assert out["t14"]["cost"] <= 8 and out["r9"]["cost"] <= 4
    assert sum(g["r9"] is not None for g in out["games"]) == 9
    assert sum(g["t14"]["double"] for g in out["games"]) <= 2
    # a game the schedule has no 1X2 pick for follows the model favourite
    assert out["games"][1]["t14"]["picks"][0] in ("1", "0") and not out["games"][1]["t14"]["fixed"]


def test_upset_picks_are_not_forced():
    pred = {"model": {"H": 0.6, "D": 0.2, "A": 0.2}, "market": {},
            "longshots": [{"selection": "A", "edge": 0.3}], "bets": []}
    assert zucai._page_pick(pred) is None
