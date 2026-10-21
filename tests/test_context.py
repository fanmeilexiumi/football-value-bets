from datetime import datetime

from footy import context


def _table(n=20, played=8):
    return [{"team": f"T{i}", "id": str(i), "rank": i, "points": 30 - i, "played": played} for i in range(1, n + 1)]


def test_motivation_zones():
    t = _table()
    assert context.motivation("E0", t, "2")["text"].endswith("争冠")
    assert context.motivation("E0", t, "17")["level"] == 2  # three points above 18th
    assert context.motivation("E0", t, "10")["level"] == 1
    assert "赛季初" in context.motivation("E0", _table(played=3), "1")["text"]
    cl = context.motivation("CL", _table(36), "9")
    assert "附加赛区" in cl["text"] and cl["level"] == 2


def test_matchup_rules():
    assert "中场人数多" in context.matchup("4-3-3", "4-4-2") or "阵型" in context.matchup("4-3-3", "4-4-2")
    assert "三中卫" in context.matchup("3-4-2-1", "4-3-3")
    assert context.matchup(None, "4-3-3") is None


def test_verdict_directions():
    c = {"home_name": "A", "away_name": "B",
         "home": {"rest_days": 2.5, "next_days": 3.0, "motivation": {"level": 1}},
         "away": {"rest_days": 6.0, "next_days": 7.0, "motivation": {"level": 2}},
         "weather": {"bad": ["降雨"]}, "market_move": {"H": -0.05, "U25": 0.0}}
    home = context.verdict({"selection": "H"}, c)
    assert home["lean"] == "削弱" and any("休息不足" in x for x in home["con"])
    away = context.verdict({"selection": "A"}, c)
    assert away["lean"] == "支持"
    assert context.verdict({"selection": "U25"}, c)["pro"]
    assert context.verdict({"selection": "O25"}, c)["con"]


def test_collect_with_mocked_sources(monkeypatch):
    kick = "2026-10-21T19:00Z"
    sb = {"events": [{"id": "1", "competitions": [{"date": kick, "venue": {"fullName": "Allianz Arena", "address": {"city": "München", "country": "Germany"}},
                                                   "competitors": [{"homeAway": "home", "team": {"id": "132", "displayName": "Bayern Munich"}},
                                                                   {"homeAway": "away", "team": {"id": "359", "displayName": "Arsenal"}}]}]}]}
    summary = {"lastFiveGames": [{"team": {"id": "132"}, "events": [{"gameDate": "2026-10-18T13:30Z", "gameResult": "W", "leagueName": "German Bundesliga"}]},
                                 {"team": {"id": "359"}, "events": [{"gameDate": "2026-10-19T15:00Z", "gameResult": "D", "leagueName": "English Premier League"}]}],
               "standings": {"groups": [{"standings": {"entries": [
                   {"team": "Bayern", "id": "132", "stats": [{"name": "rank", "value": 3}, {"name": "points", "value": 9}, {"name": "gamesPlayed", "value": 3}]},
                   {"team": "Arsenal", "id": "359", "stats": [{"name": "rank", "value": 5}, {"name": "points", "value": 7}, {"name": "gamesPlayed", "value": 3}]}]}}]}}
    sched = {"events": [{"date": "2026-10-24T14:00Z", "name": "Everton at Arsenal", "seasonType": {"name": "2026-27 English Premier League"}}]}
    wx = {"hourly": {"time": ["2026-10-21T19:00"], "temperature_2m": [8.0], "precipitation_probability": [80],
                     "precipitation": [2.0], "wind_speed_10m": [12.0]}}

    def fake(url, params=None, headers=None):
        if "scoreboard" in url:
            return sb
        if "summary" in url:
            return summary
        if "schedule" in url:
            return sched
        if "geocoding" in url:
            return {"results": [{"latitude": 48.1, "longitude": 11.6, "country": "Germany"}]}
        return wx
    monkeypatch.setattr(context, "_get", fake)
    monkeypatch.delenv("API_FOOTBALL_KEY", raising=False)
    pred = {"league": "CL", "bj_date": "2026-10-22", "bj_time": "03:00", "home": "Bayern Munich", "away": "Arsenal",
            "market": {"H": 0.48, "D": 0.24, "A": 0.28}, "bets": [{"selection": "H", "odds": 2.13, "edge": 0.07}], "longshots": []}
    out = context.collect([pred], {"CL|2026-10-22|Bayern Munich|Arsenal": {"market": {"H": 0.52}}}, datetime(2026, 10, 21, 1))
    c = out["CL|2026-10-22|Bayern Munich|Arsenal"]
    assert round(c["home"]["rest_days"], 1) == 3.2 and c["away"]["next_comp"] == "英超"
    assert c["weather"]["bad"] == ["降雨"] and c["sources"] == ["ESPN", "Open-Meteo"]
    assert any("下降" in x for x in c["verdicts"]["H"]["con"])
