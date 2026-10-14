"""Display-form dates for the rendered resume (#258).

Pure and model-free. Stored values are never rewritten; this runs only where a
date is printed on the page (LaTeX, DOCX, markdown). One display form,
"Mon YYYY" with a 3-letter English month, whatever shape the host or the parser
stored ("2024-01", "2024-01-15", "June 2021", "Sept 2021").
"""
from __future__ import annotations

import re

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_FULL = ("january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december")
_NAME_TO_MONTH = {n: i + 1 for i, n in enumerate(_FULL)}
_NAME_TO_MONTH.update({n[:3]: i + 1 for i, n in enumerate(_FULL)})
_NAME_TO_MONTH["sept"] = 9

_ISO = re.compile(r"^(\d{4})[-/](\d{1,2})(?:[-/](\d{1,2}))?$")
_MDY_NUM = re.compile(r"^(\d{1,2})/(\d{4})$")
_NAMED = re.compile(r"^([A-Za-z]{3,9})\.?(?:\s+\d{1,2}(?:st|nd|rd|th)?,?)?,?\s+(\d{4})$")
_PREFIX = re.compile(r"^((?:expected|anticipated)\b:?\s*)(.+)$", re.IGNORECASE)
# Range separators: spaced hyphen/"to", or any dash with or without spaces
# (an unspaced hyphen is the ISO "YYYY-MM" separator, so it is not one).
_RANGE_SEP = re.compile(r"(\s+(?:-{1,2}|to)\s+|\s*[–—]\s*|\s*-{2}\s*)", re.IGNORECASE)


def _mon_year(month: int, year: str) -> str:
    return f"{_MONTHS[month - 1]} {year}"


def format_date(value) -> str:
    """One date as "Mon YYYY". Bare years, "Present" and anything unparseable
    pass through verbatim; "Expected <date>" keeps its prefix."""
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    pre = _PREFIX.match(text)
    if pre:
        return pre.group(1) + format_date(pre.group(2))
    m = _ISO.match(text)
    if m:
        month = int(m.group(2))
        day = m.group(3)
        if 1 <= month <= 12 and (day is None or 1 <= int(day) <= 31):
            return _mon_year(month, m.group(1))
        return text
    m = _MDY_NUM.match(text)
    if m and 1 <= int(m.group(1)) <= 12:
        return _mon_year(int(m.group(1)), m.group(2))
    m = _NAMED.match(text)
    if m:
        month = _NAME_TO_MONTH.get(m.group(1).lower())
        if month:
            return _mon_year(month, m.group(2))
    return text


def format_date_text(value) -> str:
    """Like `format_date`, but also normalises each end of a range string such
    as a project's "2024-01 - 2024-06" or "June 2021 -- Present". Separators
    are kept as written."""
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    parts = _RANGE_SEP.split(text)
    if len(parts) == 1:
        return format_date(text)
    return "".join(p if i % 2 else format_date(p) for i, p in enumerate(parts))
