"""Team-name harmonisation between football-data.co.uk and OddsPortal."""

from __future__ import annotations

import difflib
import re
import unicodedata

# Canonical names follow football-data.co.uk. Keys are lower-cased aliases.
ALIASES: dict[str, str] = {
    # England
    "nottingham forest": "Nott'm Forest",
    "nottm forest": "Nott'm Forest",
    "manchester city": "Man City",
    "manchester united": "Man United",
    "manchester utd": "Man United",
    "tottenham hotspur": "Tottenham",
    "wolverhampton": "Wolves",
    "wolverhampton wanderers": "Wolves",
    "newcastle united": "Newcastle",
    "newcastle utd": "Newcastle",
    "west ham united": "West Ham",
    "brighton & hove albion": "Brighton",
    "brighton and hove albion": "Brighton",
    "leeds united": "Leeds",
    "leicester city": "Leicester",
    "ipswich town": "Ipswich",
    "luton town": "Luton",
    "sheffield utd": "Sheffield United",
    "afc bournemouth": "Bournemouth",
    "hull city": "Hull",
    "coventry city": "Coventry",
    "sunderland afc": "Sunderland",
    # Italy
    "inter milan": "Inter",
    "internazionale": "Inter",
    "ac milan": "Milan",
    "as roma": "Roma",
    "ss lazio": "Lazio",
    "hellas verona": "Verona",
    "atalanta bc": "Atalanta",
    "parma calcio": "Parma",
    # Spain
    "atletico madrid": "Ath Madrid",
    "atl. madrid": "Ath Madrid",
    "athletic bilbao": "Ath Bilbao",
    "athletic club": "Ath Bilbao",
    "real sociedad": "Sociedad",
    "rayo vallecano": "Vallecano",
    "celta vigo": "Celta",
    "espanyol": "Espanol",
    "real betis": "Betis",
    "alaves": "Alaves",
    "deportivo alaves": "Alaves",
    "real oviedo": "Oviedo",
    "real valladolid": "Valladolid",
    "las palmas": "Las Palmas",
    "cadiz cf": "Cadiz",
    "elche cf": "Elche",
    "levante ud": "Levante",
}


_TRANSLIT = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "ß": "ss", "đ": "d", "ł": "l", "ı": "i"})


def _plain(name: str) -> str:
    s = unicodedata.normalize("NFKD", name.translate(_TRANSLIT)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def canonical(name: str, known: set[str] | None = None) -> str:
    """Map any spelling to the football-data.co.uk name.

    `known` is the set of names already seen in historical data; when given,
    unknown names are matched to the closest known one.
    """
    if not isinstance(name, str):
        return name
    p = _plain(name)
    if p in ALIASES:
        return ALIASES[p]
    if known:
        if name in known:
            return name
        by_plain = {_plain(k): k for k in known}
        if p in by_plain:
            return by_plain[p]
        hit = difflib.get_close_matches(p, list(by_plain), n=1, cutoff=0.72)
        if hit:
            return by_plain[hit[0]]
    return name.strip()


# ---------------------------------------------------------- Champions League
# Clubs outside the three leagues get one short name each. Keys are plain
# lower-case spellings seen in openfootball results and The Odds API.
CL_ALIASES: dict[str, str] = {
    # three leagues: spellings the generic matcher misses
    "fc internazionale milano": "Inter",
    "club atletico de madrid": "Ath Madrid",
    "real sociedad de futbol": "Sociedad",
    "bologna fc 1909": "Bologna",
    # Germany
    "fc bayern munchen": "Bayern Munich", "bayern munchen": "Bayern Munich", "bayern munich": "Bayern Munich",
    "bayer 04 leverkusen": "Leverkusen", "bayer leverkusen": "Leverkusen",
    "borussia dortmund": "Dortmund", "rb leipzig": "RB Leipzig", "vfb stuttgart": "Stuttgart",
    "vfl wolfsburg": "Wolfsburg", "1. fc union berlin": "Union Berlin", "union berlin": "Union Berlin",
    "eintracht frankfurt": "Eintracht Frankfurt",
    # France
    "paris saint-germain": "Paris SG", "paris saint-germain fc": "Paris SG", "paris saint germain": "Paris SG",
    "olympique de marseille": "Marseille", "olympique marseille": "Marseille", "marseille": "Marseille",
    "as monaco fc": "Monaco", "as monaco": "Monaco", "monaco": "Monaco", "lille osc": "Lille", "lille": "Lille",
    "racing club de lens": "Lens", "rc lens": "Lens", "lens": "Lens",
    "stade brestois 29": "Brest", "stade brestois": "Brest", "brest": "Brest",
    # Portugal
    "sport lisboa e benfica": "Benfica", "sl benfica": "Benfica", "benfica": "Benfica",
    "fc porto": "Porto", "porto": "Porto",
    "sporting clube de portugal": "Sporting CP", "sporting cp": "Sporting CP", "sporting lisbon": "Sporting CP",
    "sporting clube de braga": "Braga", "sc braga": "Braga", "braga": "Braga",
    # Netherlands, Belgium
    "psv": "PSV", "psv eindhoven": "PSV", "afc ajax": "Ajax", "ajax": "Ajax",
    "feyenoord rotterdam": "Feyenoord", "feyenoord": "Feyenoord",
    "club brugge kv": "Club Brugge", "club brugge": "Club Brugge", "royal antwerp fc": "Antwerp", "royal antwerp": "Antwerp",
    "royale union saint-gilloise": "Union SG", "union saint-gilloise": "Union SG", "union st. gilloise": "Union SG",
    # elsewhere
    "fc red bull salzburg": "Salzburg", "rb salzburg": "Salzburg", "red bull salzburg": "Salzburg",
    "sk sturm graz": "Sturm Graz", "sturm graz": "Sturm Graz",
    "gnk dinamo zagreb": "Dinamo Zagreb", "dinamo zagreb": "Dinamo Zagreb",
    "fk shakhtar donetsk": "Shakhtar", "shakhtar donetsk": "Shakhtar", "dinamo kiev": "Dynamo Kyiv", "dynamo kyiv": "Dynamo Kyiv",
    "fk crvena zvezda": "Crvena Zvezda", "crvena zvezda": "Crvena Zvezda", "red star belgrade": "Crvena Zvezda",
    "fc kobenhavn": "Copenhagen", "fc copenhagen": "Copenhagen", "copenhagen": "Copenhagen",
    "bsc young boys": "Young Boys", "young boys": "Young Boys",
    "pae olympiakos sfp": "Olympiakos", "olympiakos": "Olympiakos", "olympiacos": "Olympiakos",
    "olympiakos piraeus": "Olympiakos", "olympiacos piraeus": "Olympiakos",
    "qarabag agdam fk": "Qarabag", "qarabag fk": "Qarabag", "qarabag": "Qarabag",
    "fk bodo/glimt": "Bodo/Glimt", "bodo/glimt": "Bodo/Glimt", "bodo glimt": "Bodo/Glimt",
    "paphos fc": "Pafos", "pafos fc": "Pafos", "pafos": "Pafos", "paphos": "Pafos",
    "fk kairat": "Kairat", "kairat almaty": "Kairat", "kairat": "Kairat",
    "celtic fc": "Celtic", "celtic": "Celtic", "rangers fc": "Rangers", "rangers": "Rangers",
    "galatasaray sk": "Galatasaray", "galatasaray": "Galatasaray", "besiktas": "Besiktas", "besiktas jk": "Besiktas",
    "fenerbahce": "Fenerbahce", "fenerbahce sk": "Fenerbahce",
    "ac sparta praha": "Sparta Praha", "sparta praha": "Sparta Praha", "sparta prague": "Sparta Praha",
    "sk slavia praha": "Slavia Praha", "slavia praha": "Slavia Praha", "slavia prague": "Slavia Praha",
    "viktoria plzen": "Viktoria Plzen", "sk slovan bratislava": "Slovan Bratislava", "slovan bratislava": "Slovan Bratislava",
    "zenit st. petersburg": "Zenit", "malmo ff": "Malmo", "fc sheriff": "Sheriff", "maccabi haifa": "Maccabi Haifa",
}

_CLUB_TOKENS = {"fc", "cf", "afc", "ssc", "bc", "sk", "fk", "kv"}


def _strip_club(p: str) -> str:
    return " ".join(w for w in p.split() if w not in _CLUB_TOKENS) or p


def cl_canonical(name: str, domestic: set[str] | None = None) -> str:
    """openfootball Champions League name -> one name per club.

    Clubs from the three leagues map to their football-data.co.uk name, so the
    domestic results feed the same rating; pass that league's names as
    `domestic`. Other clubs get the short name from CL_ALIASES.
    """
    p = _plain(name)
    for q in (p, _strip_club(p)):
        if q in CL_ALIASES:
            return CL_ALIASES[q]
    if domestic:
        q = _strip_club(p)
        return ALIASES.get(q) or canonical(q, domestic)
    return name.strip()


def cl_canonical_api(name: str, known: set[str]) -> str:
    """Bookmaker spelling (no country tag) -> a name from `known`, if one fits."""
    p = _plain(name)
    for q in (p, _strip_club(p)):
        hit = CL_ALIASES.get(q) or ALIASES.get(q)
        if hit:
            return hit
    by_plain = {_plain(k): k for k in known}
    for q in (p, _strip_club(p)):
        if q in by_plain:
            return by_plain[q]
    hit = difflib.get_close_matches(_strip_club(p), list(by_plain), n=1, cutoff=0.8)
    return by_plain[hit[0]] if hit else name.strip()
