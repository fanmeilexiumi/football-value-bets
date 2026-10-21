import json, requests
E="https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/{path}"
sb=requests.get(E.format(lg="uefa.champions",path="scoreboard"),params={"dates":"20261021"},timeout=20).json()
ev=sb["events"][0]; print("event",ev["id"],ev["name"])
sm=requests.get(E.format(lg="uefa.champions",path="summary"),params={"event":ev["id"]},timeout=20).json()
print("keys",list(sm.keys()))
for t in sm.get("lastFiveGames",[])[:1]:
    e=t["events"][-1] if t["events"] else None; print("lastgame sample", json.dumps(e)[:600])
    tid=t["team"]["id"]
for lg in ("all","eng.1","ita.1"):
    for path in (f"teams/{tid}/schedule",):
        try:
            r=requests.get(E.format(lg=lg,path=path),params={"fixture":"true"},timeout=20); d=r.json()
            print(lg,path,r.status_code,[ (x["date"][:10],(x.get("league") or {}).get("name") or (x.get("season") or {}).get("displayName")) for x in d.get("events",[])[:4]])
        except Exception as ex: print(lg,path,"ERR",ex)
if e:
    for lg in ("all","ita.1","eng.1","esp.1"):
        try:
            r=requests.get(E.format(lg=lg,path="summary"),params={"event":e["id"]},timeout=20); d=r.json()
            ros=d.get("rosters",[])
            print("last summary",lg,r.status_code,[ (x.get("formation"), [ (p["athlete"]["displayName"],p.get("starter"),(p.get("position") or {}).get("abbreviation")) for p in x.get("roster",[])[:3]]) for x in ros])
        except Exception as ex: print(lg,"ERR",ex)
print("summary injuries", json.dumps(sm.get("injuries"))[:400])
