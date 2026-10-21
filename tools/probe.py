import re,html,requests
s=requests.get("https://fanmeilexiumi.github.io/football-value-bets/",timeout=30).text
for d in re.findall(r'<details class="why">(.*?)</details>',s,re.S)[:8]:
    print(re.sub(r"\s+"," ",html.unescape(re.sub(r"<[^>]+>"," ",d))));print("---")
