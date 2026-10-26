"""Normalização tolerante de datas vindas das origens (ISO, datetime, DD/MM/AAAA). Nunca lança."""
import re
from datetime import date, datetime

_BR = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})")
_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})")


def normaliza_data(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v)
    m = _ISO.match(s)
    try:
        if m:
            return date(int(m[1]), int(m[2]), int(m[3]))
        m = _BR.match(s)
        if m:
            return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None
    return None


def normaliza_data_iso(v) -> str | None:
    d = normaliza_data(v)
    return d.isoformat() if d else None
