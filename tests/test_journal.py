from datetime import date, datetime

import pandas as pd

from footy import config, journal, learn, zucai


def _pred(home, away, bj_date="2026-10-21", bj_time="03:00", league="CL", model=None, market=None, bets=(), longshots=()):
    return {"league": league, "league_name": "欧冠", "date": "2026-10-20", "home": home, "away": away,
            "bj_date": bj_date, "bj_time": bj_time, "model": model, "market": market or {},
            "bets": list(bets), "longshots": list(longshots)}


def test_record_settle_review(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "JOURNAL_DIR", tmp_path)
    preds = [
        _pred("Liverpool", "Villarreal", model={"H": 0.6, "D": 0.22, "A": 0.18, "O25": 0.6},
              market={"H": 0.72, "D": 0.17, "A": 0.11},
              longshots=[{"selection": "A", "odds": 8.0, "edge": 0.2, "tags": ["冷门"]}]),
        _pred("Arsenal", "Chelsea", league="E0", bj_time="20:30", model={"H": 0.5, "D": 0.25, "A": 0.25, "O25": 0.5},
              market={"H": 0.48, "D": 0.27, "A": 0.25},
              bets=[{"selection": "H", "odds": 2.3, "edge": 0.15}]),
    ]
    recs = []
    journal.record(recs, preds, datetime(2026, 10, 20, 11))
    assert {r["day"] for r in recs} == {"2026-10-20", "2026-10-21"}  # 03:00 belongs to the evening before
    # a started game keeps its last snapshot
    journal.record(recs, [_pred("Liverpool", "Villarreal", model={"H": 0.1, "D": 0.1, "A": 0.8})], datetime(2026, 10, 21, 4))
    assert recs[0]["model"]["H"] == 0.6

    hist = pd.DataFrame([{"league": "E0", "date": pd.Timestamp("2026-10-21"), "home": "Arsenal", "away": "Chelsea", "hg": 2, "ag": 0}])
    journal.settle(recs, hist, datetime(2026, 10, 22, 11), api_key="")
    by = {r["home"]: r for r in recs}
    assert by["Arsenal"]["result"]["hg"] == 2 and by["Liverpool"]["result"] is None
    by["Liverpool"]["result"] = {"hg": 0, "ag": 1, "source": "test"}

    rv = journal.build_review(recs, [], datetime(2026, 10, 22, 11))
    assert rv["latest"]["day"] == "2026-10-21"
    g = rv["latest"]["games"][0]
    assert g["call_hit"] and g["picks"][0]["won"] and abs(g["picks"][0]["profit"] - 1.3) < 1e-9
    lv = [review for review in (journal.review_game(r) for r in recs) if review["home"] == "Liverpool"][0]
    assert lv["upset"] and lv["upset"]["flagged"] and lv["upset"]["cause"].startswith("已提示")
    assert rv["total"]["n"] == 2 and rv["total"]["upsets_flagged"] == 1


def test_void_after_days():
    recs = []
    journal.record(recs, [_pred("A", "B", model=None, market={"H": .5, "D": .3, "A": .2})], datetime(2026, 10, 20, 11))
    journal.settle(recs, pd.DataFrame(columns=["league", "date", "home", "away", "hg", "ag"]), datetime(2026, 10, 30), api_key="")
    assert recs[0]["result"] == {"void": True}


def test_lottery_score_and_settle():
    row = ('<tr class="bet-tb-tr" data-pjgl="50,25,25"><td class="td td-no">1</td><td class="td td-evt"><a>欧冠</a></td>'
           '<td class="td td-endtime">10-21 03:00</td><td class="td td-team"><a class="team-l">利物浦</a>'
           '<i class="team-vs">0:1</i><a class="team-r">比利亚雷亚尔</a></td></tr>')
    p = zucai.parse_period('<li class="chked" data-expect="26146"></li>' + row, date(2026, 10, 22))
    assert p["games"][0]["score"] == (0, 1)
    per = [{"expect": "26146", "t14": "0", "r9": "-", "settled": False, "games": [
        {"no": 1, "comp": "欧冠", "kickoff": "2026-10-21 03:00", "home_cn": "利物浦", "away_cn": "比利亚雷亚尔",
         "pred_id": None, "p": {"3": .6, "1": .2, "0": .2}, "fav": "3", "t14": ["3", "0"], "r9": None, "result": None}]}]
    journal.settle_lottery(per, [], datetime(2026, 10, 22, 11), fetch=lambda e: p)
    assert per[0]["settled"] and per[0]["games"][0]["result"] == "0"
    rv = journal._lottery_review(per[0])
    assert rv["t14_hits"] == 1 and rv["games"][0]["upset"]


def test_tune_only_adopts_validated_changes(monkeypatch):
    rng = __import__("numpy").random.default_rng(1)
    rows = []
    for i in range(400):
        d = pd.Timestamp("2025-01-01") + pd.Timedelta(days=i)
        mkt = [0.5, 0.27, 0.23]
        res = int(rng.choice(3, p=mkt))
        rows.append({"date": d, "league": "E0", "res": res, "over": int(rng.random() < .5),
                     "dc": [0.4, 0.3, 0.3], "elo": [0.45, 0.28, 0.27], "blend": [0.41, 0.3, 0.29], "mkt": mkt,
                     "dc_over": 0.5, "mkt_over": 0.5, "page_bets": [],
                     "odds": {"avg_h": 1.9, "avg_d": 3.5, "avg_a": 4.1, "max_h": 2.0, "max_d": 3.7, "max_a": 4.4,
                              "avg_over": 1.9, "avg_under": 1.9, "max_over": 2.0, "max_under": 2.0}})
    before = {k: getattr(config, k) for k in learn.PARAMS}
    try:
        state = learn.tune(rows, [], date(2026, 2, 5), {})
        names = [c["param"] for c in state["log"][0]["checks"]]
        assert names == list(learn.PARAMS)
        # the market is the truth here, so leaning on it must be adopted
        ms = [c for c in state["log"][0]["checks"] if c["param"] == "ZC_MARKET_SHARE"][0]
        assert ms["adopted"] and state["params"]["ZC_MARKET_SHARE"] > 0
        assert all(c["adopted"] or state["params"][c["param"]] == before[c["param"]] for c in state["log"][0]["checks"])
    finally:
        for k, v in before.items():
            setattr(config, k, v)
