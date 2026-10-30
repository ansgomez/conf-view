"""
Generic call-for-papers scraper for ConfView.

Each conference in data/conferences.json carries a list of candidate `sources`
URLs (and an optional `url_template`, with or without a `{year}` placeholder).
The scraper fetches each candidate page, finds the "important dates" lines by
keyword and extracts four milestones:

    submission -> notification -> camera_ready -> conference (start/end)

A date parsed from the page is marked ``"confirmed"``. Anything the page does
not show keeps its seed value and status (see scrape_conference for how a page
describing a newer edition is handled).

Layouts handled:

  * "Paper Submission Deadline: January 27, 2026"      (label + date on one line)
  * a table row with the label on one line and the date(s) on the next; only a
    bare date line or a line of the same stage is accepted there, any other
    labelled line (another milestone, an abstract, a workshop, ...) ends the scan
  * revised deadlines where the *old* date is struck through and the *new* date
    follows it -> we always keep the LAST date in the window
  * month-first, day-first and ISO dates, and ranges across two months
  * numeric dates: dotted ones day-first; slash ones only when unambiguous or
    when the page's other dates show its convention (never guessed). A date
    must stand on its own: text glued to it ("abc27 January 2026", "123May 5,
    2026") is not a date.

Scraping arbitrary conference websites is inherently brittle: when an organiser
redesigns their page the keyword match may miss. In that case the scraper keeps
the previous value rather than crashing or guessing.
"""
from __future__ import annotations

import contextvars
import datetime as _dt
import logging
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("confview.scrapers")

USER_AGENT = "ConfView/1.0 (+https://github.com/ansgomez/conf-view) conference-deadline-tracker"
TIMEOUT = 20
MAX_BYTES = 5_000_000  # CFP pages are small; don't load anything huge into memory

# Listing and aggregator sites: useful as a human reference, but their dates are
# second-hand, so they are never scraped as if they were the official page.
# Matched against the lower-cased hostname, as the domain itself or any
# subdomain of it (so "www.wikicfp.com" and "DBLP.ORG" are both caught).
NON_OFFICIAL_HOSTS = ("myhuiban.com", "dblp.org", "dblp.uni-trier.de", "dblp.dagstuhl.de",
                      "wikicfp.com", "conferenceindex.org")

# A page may move an entry forward by one edition only when the evidence fits a
# yearly cycle: the paper deadline lies within this many days before the new
# conference (labelled range), or within one year after the seed's conference
# start (deadline-only page). The same window also bounds every other stage date
# accepted from a page relative to the edition's conference start.
MAX_DEADLINE_TO_CONF_DAYS = 460

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Every date token must be bounded by non-alphanumerics on both sides (start of
# the form: _B; end: _E), so "abc27 January 2026", "123May 5, 2026" and
# "abc2026-01-27def" are not dates. Punctuation, ordinals and range separators
# sit inside the form and are unaffected.
_B = r"(?<![A-Za-z0-9])"
_E = r"(?![A-Za-z0-9])"
_MON = (r"(?<![A-Za-z])(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
        r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?")
_D = r"(?<!\d)\d{1,2}(?:st|nd|rd|th)?"
_Y = rf"\d{{4}}{_E}"
_SEP = r"\s*(?:–|—|-|to|until)\s*"

# Date forms seen on CFP pages. Each yields one date or one range; overlapping
# shorter matches are discarded, and two full dates joined by a dash or "to"
# ("May 17, 2027 - May 20, 2027") are merged into a range afterwards.
#   2026-01-27 | January 29 - February 2, 2026 | June 22-24, 2026 | Jan 27, 2026
#   29 June - 2 July 2026 | 22-24 June 2026 | 27 January 2026
_DATE_FORMS = [
    re.compile(rf"{_B}(?P<y>\d{{4}})-(?P<m>\d{{2}})-(?P<d>\d{{2}}){_E}"),
    re.compile(rf"{_B}(?P<m1>{_MON})\s+(?P<d1>{_D}){_SEP}(?P<m2>{_MON})\s+(?P<d2>{_D}),?\s+(?P<y>{_Y})", re.I),
    re.compile(rf"{_B}(?P<m1>{_MON})\s+(?P<d1>{_D})(?:{_SEP}(?P<d2>{_D}))?,?\s+(?P<y>{_Y})", re.I),
    re.compile(rf"{_B}(?P<d1>{_D})\s+(?P<m1>{_MON}){_SEP}(?P<d2>{_D})\s+(?P<m2>{_MON}),?\s+(?P<y>{_Y})", re.I),
    re.compile(rf"{_B}(?P<d1>{_D})(?:{_SEP}(?P<d2>{_D}))?\s+(?P<m1>{_MON}),?\s+(?P<y>{_Y})", re.I),
]

# Numeric dates. Dotted dates are day-first (27.01.2026, 27.1.26, and ranges
# 22.-24.06.2026 or 22.06.-24.06.2026); two-digit years mean 20yy. Slash dates
# (03/04/2026) are day-first in Europe and month-first in the US: they are read
# only when one number is above 12, or when the rest of the page shows which
# convention it uses (see _slash_convention); otherwise they are skipped, since
# a wrong "confirmed" date is worse than none. The boundaries also refuse a dot
# or digit next to the date, so version and IP numbers (v2.1.2026,
# 10.1.12.2026) are not read as dates.
_NB = r"(?<![A-Za-z0-9.])"
_NE = r"(?![A-Za-z0-9]|\.\d)"
_NY = r"(?P<y>\d{4}|\d{2})"
_DOT_FORMS = [
    re.compile(rf"{_NB}(?P<d1>\d{{1,2}})\.(?P<m1>\d{{1,2}})\.?{_SEP}(?P<d2>\d{{1,2}})\.(?P<m2>\d{{1,2}})\.{_NY}{_NE}"),
    re.compile(rf"{_NB}(?P<d1>\d{{1,2}})\.?{_SEP}(?P<d2>\d{{1,2}})\.(?P<m1>\d{{1,2}})\.{_NY}{_NE}"),
    re.compile(rf"{_NB}(?P<d1>\d{{1,2}})\.(?P<m1>\d{{1,2}})\.{_NY}{_NE}"),
]
_SLASH = re.compile(rf"{_NB}(?<!/)(?P<a>\d{{1,2}})/(?P<b>\d{{1,2}})/(?P<y>\d{{4}}){_NE}(?!/)")

# Convention for ambiguous slash dates on the page being parsed ("dmy", "mdy"
# or None), set by extract_important_dates.
_slash_order: contextvars.ContextVar = contextvars.ContextVar("slash_order", default=None)


def _slash_convention(text: str) -> Optional[str]:
    """Decide how to read ambiguous slash dates on this page.

    Unambiguous slash dates (one number above 12) decide if they all agree.
    Otherwise the page's other dates vote: dotted and "27 January 2026" dates
    count as day-first, "January 27, 2026" as month-first. Only a one-sided
    vote decides; a mixed or empty page leaves ambiguous dates unread.
    """
    dmy = mdy = 0
    for m in _SLASH.finditer(text):
        a, b = int(m["a"]), int(m["b"])
        dmy += a > 12 >= b
        mdy += b > 12 >= a
    if dmy or mdy:
        return "dmy" if dmy and not mdy else "mdy" if mdy and not dmy else None
    dmy = sum(1 for f in _DOT_FORMS for _ in f.finditer(text))
    dmy += sum(1 for f in _DATE_FORMS[3:] for _ in f.finditer(text))
    mdy = sum(1 for f in _DATE_FORMS[1:3] for _ in f.finditer(text))
    return "dmy" if dmy and not mdy else "mdy" if mdy and not dmy else None


def _numeric_hits(text: str) -> List[Tuple[int, int, str, Optional[str]]]:
    hits = []
    for form in _DOT_FORMS:
        for m in form.finditer(text):
            g = m.groupdict()
            y = int(g["y"]) + (2000 if len(g["y"]) == 2 else 0)
            m1 = int(g["m1"])
            m2 = int(g["m2"]) if g.get("m2") else m1
            start = _iso(y, m1, int(g["d1"]))
            end = _iso(y, m2, int(g["d2"])) if g.get("d2") else None
            if start and end and end < start:
                end = None
            if start:
                hits.append((m.start(), m.end(), start, end))
    order = _slash_order.get()
    for m in _SLASH.finditer(text):
        a, b, y = int(m["a"]), int(m["b"]), int(m["y"])
        if a > 12 >= b or (a <= 12 and b <= 12 and (a == b or order == "dmy")):
            start = _iso(y, b, a)
        elif b > 12 >= a or (a <= 12 and b <= 12 and order == "mdy"):
            start = _iso(y, a, b)
        else:
            start = None  # ambiguous, and the page gives no convention
        if start:
            hits.append((m.start(), m.end(), start, None))
    return hits

# Keyword sets that identify each milestone label in the important-dates block.
# Order matters: the first stage whose keyword appears on a line wins. A line
# that also contains one of the stage's EXCLUDE words is not that stage (it is a
# workshop, poster or abstract deadline, or a notice about camera-ready).
STAGE_KEYWORDS: Dict[str, List[str]] = {
    "submission":   ["paper submission", "submission deadline", "full paper",
                     "paper registration", "manuscript submission", "papers due"],
    "notification": ["acceptance notification", "author notification", "notification"],
    "camera_ready": ["camera ready", "camera-ready", "final version", "final manuscript"],
    "conference":   ["conference date", "conference dates", "conference:",
                     "will be held", "will take place", "takes place"],
}
_SIDE_EVENTS = ["workshop", "poster", "demo", "tutorial", "artifact", "artefact",
                "doctoral", "phd forum", "work-in-progress", "wip", "late-breaking"]
STAGE_EXCLUDE: Dict[str, List[str]] = {
    "submission":   _SIDE_EVENTS + ["abstract"],
    "notification": _SIDE_EVENTS + ["camera", "instruction"],
    "camera_ready": _SIDE_EVENTS,
    "conference":   [],
}
# Other labels that make a line "about something else" when scanning a table
# layout for the date that belongs to a label on the previous line.
_OTHER_LABELS = ["abstract", "registration", "rebuttal", "review", "response",
                 "opens", "early bird", "early-bird"]
_ALL_LABELS = sorted({k for kws in STAGE_KEYWORDS.values() for k in kws}
                     | set(_SIDE_EVENTS) | set(_OTHER_LABELS))


def _iso(year: int, month: int, day: int) -> Optional[str]:
    try:
        return _dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def _month(name: str) -> int:
    # "Sept", "June" and "Jan." all reduce to their first three letters.
    return MONTHS[name[:3].lower()]


def _day(token: str) -> int:
    return int(re.match(r"\d+", token).group())


def _find_dates(text: str) -> List[Tuple[str, Optional[str]]]:
    """Return every date or range in ``text`` as (start, end-or-None), in order."""
    hits = []
    for form in _DATE_FORMS:
        for m in form.finditer(text):
            g = m.groupdict()
            y = int(g["y"])
            if "m" in g:
                start, end = _iso(y, int(g["m"]), int(g["d"])), None
            else:
                m1 = _month(g["m1"])
                m2 = _month(g["m2"]) if g.get("m2") else m1
                start = _iso(y, m1, _day(g["d1"]))
                end = _iso(y, m2, _day(g["d2"])) if g.get("d2") else None
                # "December 30 - January 2, 2027": the start is in the previous year
                if start and end and end < start and m2 < m1:
                    start = _iso(y - 1, m1, _day(g["d1"]))
                elif start and end and end < start:
                    end = None  # "June 24-22, 2026": not a range we can trust
            if start:
                hits.append((m.start(), m.end(), start, end))
    hits.extend(_numeric_hits(text))
    # keep the longest match at each position, drop matches inside a kept one
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    kept, last_end = [], -1
    for h in hits:
        if h[0] >= last_end:
            kept.append(list(h))
            last_end = h[1]
    merged = []
    for h in kept:
        prev = merged[-1] if merged else None
        gap = text[prev[1]:h[0]] if prev else ""
        if (prev and prev[3] is None and h[3] is None and h[2] > prev[2]
                and (re.fullmatch(_SEP, gap) or gap.strip() == "/")):
            prev[1], prev[3] = h[1], h[2]
        else:
            merged.append(h)
    return [(h[2], h[3]) for h in merged]


def _parse_single(text: str) -> Optional[str]:
    """Return the LAST date found in ``text`` as an ISO string.

    Using the last match handles revised deadlines where the superseded date
    is shown first (often struck through) and the current one follows.
    """
    found = _find_dates(text)
    return found[-1][0] if found else None


def _parse_range(text: str) -> Optional[Tuple[str, str]]:
    """Return (start, end) ISO dates for the last date range in ``text``,
    falling back to the last single date used as both start and end."""
    found = _find_dates(text)
    ranges = [f for f in found if f[1]]
    if ranges:
        return ranges[-1]
    if found:
        return found[-1][0], found[-1][0]
    return None


def fetch(url: str) -> Optional[str]:
    """Fetch a URL and return its visible text, or ``None`` on failure."""
    try:
        with requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT,
                          stream=True) as resp:
            resp.raise_for_status()
            body = resp.raw.read(MAX_BYTES + 1, decode_content=True)
            # requests assumes ISO-8859-1 when the header has no charset, which
            # garbles UTF-8 dashes; in that case let BeautifulSoup read <meta charset>.
            declared = "charset" in resp.headers.get("content-type", "").lower()
            encoding = resp.encoding if declared else None
    except requests.RequestException as exc:
        log.warning("fetch failed for %s: %s", url, exc)
        return None
    if len(body) > MAX_BYTES:
        log.warning("fetch skipped for %s: page larger than %d bytes", url, MAX_BYTES)
        return None
    soup = BeautifulSoup(body, "html.parser", from_encoding=encoding)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    # Keep line structure: labels and their dates often sit on adjacent lines.
    return soup.get_text("\n")


def _lines(text: str) -> List[str]:
    out = []
    for raw in text.splitlines():
        s = re.sub(r"[ \t]+", " ", raw).strip()
        if s:
            out.append(s)
    return out


def _stage_of(line_low: str) -> Optional[str]:
    for stage, kws in STAGE_KEYWORDS.items():
        if any(k in line_low for k in kws):
            if any(x in line_low for x in STAGE_EXCLUDE[stage]):
                return None
            return stage
    return None


def _labelled(line_low: str) -> bool:
    """True if the line names any milestone, side event or other labelled item."""
    return any(k in line_low for k in _ALL_LABELS)


def _date_line_for(lines: List[str], i: int, stage: str) -> Tuple[Optional[str], bool]:
    """Return (line carrying ``stage``'s date or None, found_inline).

    If the label line itself contains a date (inline "Label: Jan 27, 2026"
    layout), that line is used. Otherwise we scan forward two lines (table
    layout, label on its own line) and accept only a *bare* date line (one that
    carries no label at all) or a line of the same, non-excluded stage. Any
    other labelled line - another milestone, an abstract or registration line,
    a workshop or poster deadline - ends the scan with no result, so a date
    that belongs to something else is never taken for ``stage``.

    In both cases, when the date line is directly followed by further bare
    date lines, the LAST of them is returned: a struck-through old deadline and
    the new one usually land on separate lines once the HTML is flattened.
    """
    def bare(j: int) -> bool:
        return j < len(lines) and not _labelled(lines[j].lower()) and bool(_find_dates(lines[j]))

    def extend(j: int) -> str:
        while bare(j + 1):
            j += 1
        return lines[j]

    if _find_dates(lines[i]):
        return extend(i), True
    for j in range(i + 1, min(i + 3, len(lines))):
        low = lines[j].lower()
        if _labelled(low) and _stage_of(low) != stage:
            break
        if _find_dates(lines[j]):
            return extend(j), False
    return None, False


def extract_important_dates(text: str) -> Dict[str, object]:
    """Extract milestone dates from the visible page text.

    Returns a dict like::

        {"submission": "2026-01-27", "notification": "2026-03-28",
         "camera_ready": "2026-04-15", "conference": ("2026-06-22", "2026-06-24")}

    Only keys that were confidently found are present. Where a deadline was
    revised (old date struck through, new date after it) the LAST date on the
    line is taken. For each stage the first inline match (label and date on
    one line) wins; a date picked up from the line *below* a label (table
    layout) is kept only until an inline match for the same stage appears.

    If no labelled conference line is found, the first date range near the top
    of the page is returned under "conference" with ``conference_guessed``
    set; callers treat that as a hint about which edition the page is for,
    never as the conference dates themselves.
    """
    token = _slash_order.set(_slash_convention(text))
    try:
        return _extract(_lines(text))
    finally:
        _slash_order.reset(token)


def _extract(lines: List[str]) -> Dict[str, object]:
    found: Dict[str, object] = {}
    inline: set = set()

    for i, line in enumerate(lines):
        stage = _stage_of(line.lower())
        if not stage or stage in inline:
            continue
        date_line, is_inline = _date_line_for(lines, i, stage)
        if not date_line or (stage in found and not is_inline):
            continue
        if stage == "conference":
            # conferences last several days; a lone date on the line is more
            # likely a deadline or a news item than the event itself
            rng = _parse_range(date_line)
            if rng and rng[0] != rng[1]:
                found[stage] = rng
            else:
                continue
        else:
            d = _parse_single(date_line)
            if d:
                found[stage] = d
            else:
                continue
        if is_inline:
            inline.add(stage)

    # Fallback for the conference range: first real date-range near the top.
    # It is only a guess, so it is flagged: it is never written as the
    # conference dates and never moves the edition year.
    if "conference" not in found:
        for line in lines[:60]:
            rng = _parse_range(line)
            if rng and rng[0] and rng[0] != rng[1]:
                found["conference"] = rng
                found["conference_guessed"] = True
                break

    return found


def _is_aggregator(url: str) -> bool:
    """True if the URL's host is a listing/aggregator site (or a subdomain of
    one), compared case-insensitively on the parsed hostname."""
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in NON_OFFICIAL_HOSTS)


def _candidate_urls(conf: dict, this_year: int) -> List[str]:
    urls: List[str] = []
    tmpl = conf.get("url_template")
    if tmpl and "{year}" in tmpl:
        # try next edition first, then current
        for y in (this_year + 1, this_year):
            urls.append(tmpl.format(year=y))
    urls.extend(conf.get("sources", []))
    if tmpl and "{year}" not in tmpl:
        urls.append(tmpl)
    # de-dupe, keep order, drop aggregator sites
    seen, ordered = set(), []
    for u in urls:
        if _is_aggregator(u):
            continue
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    return ordered


def _shift_year(iso: Optional[str], years: int) -> Optional[str]:
    if not iso or not years:
        return iso
    d = _dt.date.fromisoformat(iso)
    try:
        return d.replace(year=d.year + years).isoformat()
    except ValueError:  # 29 February
        return d.replace(year=d.year + years, day=28).isoformat()


def _consistent(dates: Dict[str, object]) -> Dict[str, object]:
    """Drop stages that contradict the submission date found on the same page
    (a notification or camera-ready before it, or a conference before it)."""
    sub = dates.get("submission")
    if not isinstance(sub, str):
        return dates
    out = {"submission": sub}
    if dates.get("conference_guessed"):
        out["conference_guessed"] = True
    for stage in ("notification", "camera_ready"):
        d = dates.get(stage)
        if isinstance(d, str) and d >= sub:
            out[stage] = d
    rng = dates.get("conference")
    if isinstance(rng, tuple) and rng[0] >= sub:
        out["conference"] = rng
    return out


def _page_year(dates: Dict[str, object]) -> Optional[int]:
    rng = dates.get("conference")
    if isinstance(rng, tuple):
        return int(rng[0][:4])
    return None


def _days(a: str, b: str) -> int:
    return (_dt.date.fromisoformat(a) - _dt.date.fromisoformat(b)).days


def _edition_of(conf: dict, dates: Dict[str, object], seed_year: int) -> Optional[int]:
    """Decide which edition a page describes: ``seed_year``, ``seed_year + 1``,
    or ``None`` when the page claims a later edition without plausible evidence
    (the caller then drops the whole page).

    Moving an entry forward replaces its confirmed dates, so the evidence must
    fit a yearly cycle, and the entry moves at most ONE edition per run:

      * labelled conference range in year seed_year + 1: for an entry that
        tracks a paper deadline the page must also give one that is newer than
        the seed's and falls 0..MAX_DEADLINE_TO_CONF_DAYS days before the new
        conference; an entry without a paper deadline (journal-first event)
        needs only the labelled range;
      * labelled range two or more years ahead: rejected;
      * deadline only (no labelled range): the deadline must fall after the
        seed's conference start and no later than one year after it; an
        earlier one belongs to the seed edition, a later one is rejected.

    A guessed (unlabelled) range must have been removed from ``dates`` before
    calling; it is never evidence for a newer edition.
    """
    rng, sub = dates.get("conference"), dates.get("submission")
    seed_start = conf["conference"].get("start")
    if isinstance(rng, tuple):
        year = int(rng[0][:4])
        if year <= seed_year:
            return seed_year
        if year != seed_year + 1:
            return None
        if "submission" not in conf["stages"]:
            return year  # no paper deadline to check against (journal-first events)
        if not isinstance(sub, str):
            return None
        seed_sub = conf["stages"]["submission"].get("date")
        if seed_sub and sub <= seed_sub:
            return None
        return year if 0 <= _days(rng[0], sub) <= MAX_DEADLINE_TO_CONF_DAYS else None
    if not isinstance(sub, str) or not seed_start or sub <= seed_start:
        return seed_year
    return seed_year + 1 if sub <= _shift_year(seed_start, 1) else None


def _in_window(dates: Dict[str, object], conf_start: Optional[str]) -> Dict[str, object]:
    """Drop stage dates that cannot belong to an edition starting on
    ``conf_start``: a deadline after the conference starts, or more than
    MAX_DEADLINE_TO_CONF_DAYS before it (a stray date from another edition)."""
    if not conf_start:
        return dates
    out = {}
    for k, v in dates.items():
        if isinstance(v, str) and k != "conference_guessed":
            if not 0 <= _days(conf_start, v) <= MAX_DEADLINE_TO_CONF_DAYS:
                continue
        out[k] = v
    return out


def _better(new: Dict[str, object], new_year: int, old: Dict[str, object], old_year: int) -> bool:
    """True if page ``new`` (edition ``new_year``) should replace page ``old``:
    a later edition always wins; within the same edition the later paper
    deadline wins (a revised one); otherwise the first page found is kept."""
    if not old:
        return True
    if new_year != old_year:
        return new_year > old_year
    a, b = new.get("submission"), old.get("submission")
    return isinstance(a, str) and isinstance(b, str) and a > b


def scrape_conference(conf: dict, today: Optional[_dt.date] = None) -> Tuple[dict, Optional[str]]:
    """Return (updated copy of ``conf``, URL the dates came from or None).

    Dates parsed from the official page are marked ``confirmed``. Rules that keep
    an unattended run from making the data worse:

      * only stages that already exist in the seed are filled in (an entry with
        no paper deadline, such as a journal-first event, never gains one);
      * a page describing an OLDER edition than the seed is ignored, so the
        edition year never moves backwards;
      * a page claiming a LATER edition is used only on strong evidence (see
        _edition_of), and moves the entry at most one edition per run; a page
        that fails that check is dropped entirely - none of its dates are
        merged, not even those that would fit the current edition;
      * a stage date is accepted only if it falls between the edition's
        conference start and MAX_DEADLINE_TO_CONF_DAYS before it;
      * a conference range that was only guessed from an unlabelled line is
        never written; only a labelled "conference dates" line sets it;
      * among several candidate pages the latest edition wins, and one with a
        submission deadline still in the future ends the search;
      * when the page shows a later edition, stages it does not mention are
        moved forward by the same number of years and marked ``estimated``;
      * a date that is already ``confirmed`` (checked by a person) is never
        overwritten; a different value on the page is logged as a CONFLICT for
        review instead (e.g. an extended deadline);
      * if no page parses, the seed is kept unchanged.

    The seed's edition is the year of its conference start (``edition_year``
    is only used when the start is missing), so a cross-year edition such as
    "Dec 30, 2026 - Jan 2, 2027" is handled consistently either way.
    """
    today = today or _dt.date.today()
    seed_start = conf["conference"].get("start")
    seed_year = int(str(seed_start)[:4] if seed_start else conf.get("edition_year") or 0)
    updated = {**conf, "stages": {k: dict(v) for k, v in conf["stages"].items()},
               "conference": dict(conf["conference"])}

    best: Dict[str, object] = {}
    best_year = seed_year
    used_url: Optional[str] = None
    for url in _candidate_urls(conf, today.year):
        text = fetch(url)
        if not text:
            continue
        dates = _consistent(extract_important_dates(text))
        dates = {k: v for k, v in dates.items()
                 if k in ("conference", "conference_guessed") or k in conf["stages"]}
        if not (dates.get("submission") or dates.get("conference")):
            continue
        year = _page_year(dates)
        sub = dates.get("submission")
        prev_conf_end = _shift_year(conf["conference"].get("end"), -1)
        if (year is not None and year < seed_year) or (
                year is None and isinstance(sub, str) and prev_conf_end and sub <= prev_conf_end):
            log.info("%s: %s shows an edition older than %d; ignored", conf["id"], url, seed_year)
            continue
        if dates.get("conference_guessed"):
            # Only a hint about the edition (used above); never merged.
            dates = {k: v for k, v in dates.items() if k not in ("conference", "conference_guessed")}
            if not dates.get("submission"):
                continue
        new_year = _edition_of(conf, dates, seed_year)
        if new_year is None:
            log.info("%s: %s claims a later edition than %d without plausible evidence; page ignored",
                     conf["id"], url, seed_year)
            continue
        rng = dates.get("conference")
        start = rng[0] if isinstance(rng, tuple) else _shift_year(seed_start, new_year - seed_year)
        dates = _in_window(dates, start)
        if not (dates.get("submission") or dates.get("conference")):
            continue
        if _better(dates, new_year, best, best_year):
            best, best_year, used_url = dates, new_year, url
        sub = best.get("submission")
        if isinstance(sub, str) and _dt.date.fromisoformat(sub) >= today:
            break
    if not best:
        log.info("%s: no page parsed, keeping seed data", conf["id"])
        return updated, None

    rng = best.get("conference")
    shift = best_year - seed_year

    for stage, st in updated["stages"].items():
        d = best.get(stage)
        if not shift and st.get("status") == "confirmed":
            if isinstance(d, str) and d != st.get("date"):
                log.warning("CONFLICT %s %s: data %s (confirmed), page %s says %s",
                            conf["id"], stage, st.get("date"), used_url, d)
            continue
        if isinstance(d, str):
            st["date"], st["status"] = d, "confirmed"
        elif shift:
            st["date"], st["status"] = _shift_year(st.get("date"), shift), "estimated"

    if not shift and updated["conference"].get("status") == "confirmed":
        c = updated["conference"]
        if isinstance(rng, tuple) and rng != (c["start"], c["end"]):
            log.warning("CONFLICT %s conference: data %s..%s (confirmed), page %s says %s..%s",
                        conf["id"], c["start"], c["end"], used_url, rng[0], rng[1])
    elif isinstance(rng, tuple):
        updated["conference"].update(start=rng[0], end=rng[1], status="confirmed")
    elif shift:
        c = updated["conference"]
        c.update(start=_shift_year(c["start"], shift), end=_shift_year(c["end"], shift),
                 status="estimated")
    if shift:
        if conf.get("edition_year") is not None:
            updated["edition_year"] = int(conf["edition_year"]) + shift
        else:
            updated["edition_year"] = best_year
        log.info("%s: edition %d -> %d", conf["id"], seed_year, best_year)
    return updated, used_url


def scrape_all(data: dict, today: Optional[_dt.date] = None) -> Tuple[dict, Dict[str, Optional[str]]]:
    """Scrape every conference. Returns (new data dict, {id: source URL or None}).

    Nothing is written to disk, and ``last_updated`` is left for the caller to
    set, so a run that changes no date produces no diff.
    """
    today = today or _dt.date.today()
    out = {**data, "conferences": []}
    sources: Dict[str, Optional[str]] = {}
    for conf in data.get("conferences", []):
        try:
            conf_new, url = scrape_conference(conf, today)
        except Exception:  # never let one bad site abort the whole run
            log.exception("scrape failed for %s", conf.get("id"))
            conf_new, url = conf, None
        out["conferences"].append(conf_new)
        sources[conf.get("id")] = url
    return out, sources
