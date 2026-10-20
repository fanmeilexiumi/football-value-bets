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


def _plain(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
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
