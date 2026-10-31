"""Offline tests for scrapers.py: date parsing, stage keywords and the merge
rules. The network is stubbed. Run with: python tests/test_scrapers.py"""
import copy, datetime as dt, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import scrapers as S
ok = True
def check(name, got, want):
    global ok
    good = got == want
    ok &= good
    print(("PASS " if good else "FAIL ") + name + ("" if good else f": got {got!r}, want {want!r}"))

# date forms
check("mdy", S._parse_single("Paper submission: January 27, 2026"), "2026-01-27")
check("dmy", S._parse_single("Paper submission: 27 January 2026"), "2026-01-27")
check("dmy ordinal", S._parse_single("Paper submission: 27th Jan. 2026"), "2026-01-27")
check("iso", S._parse_single("Paper submission: 2026-01-27"), "2026-01-27")
check("sept", S._parse_single("Sept 5, 2026"), "2026-09-05")
check("revised keeps last", S._parse_single("Jan 20, 2026 Jan 27, 2026"), "2026-01-27")
check("range mdy", S._parse_range("June 22-24, 2026"), ("2026-06-22", "2026-06-24"))
check("range ordinals", S._parse_range("June 22nd–24th, 2026"), ("2026-06-22", "2026-06-24"))
check("range cross-month mdy", S._parse_range("June 29 - July 2, 2026"), ("2026-06-29", "2026-07-02"))
check("range cross-month dmy", S._parse_range("29 June – 2 July 2026"), ("2026-06-29", "2026-07-02"))
check("range dmy", S._parse_range("22-24 June 2026"), ("2026-06-22", "2026-06-24"))
check("range across new year", S._parse_range("December 30 - January 2, 2027"), ("2026-12-30", "2027-01-02"))
check("no 'toot' range", S._parse_range("May 1 toot 3, 2026"), None)

# stage keywords
page = """Important Dates
Workshop paper submission: May 1, 2026
Abstract registration: January 20, 2026
Main track paper submission: January 27, 2026
Notification of camera-ready instructions: February 1, 2026
Author notification: March 28, 2026
Camera-ready: April 15, 2026
Conference dates: 22-24 June 2026"""
check("extract", S.extract_important_dates(page), {"submission": "2026-01-27", "notification": "2026-03-28",
      "camera_ready": "2026-04-15", "conference": ("2026-06-22", "2026-06-24")})

seed = {"id": "x", "name": "X", "location": "Testville", "edition_year": 2027, "url_template": "https://{year}.x.org/", "sources": ["https://x.org/cfp"],
        "stages": {"submission": {"date": "2026-11-01", "status": "estimated"},
                   "notification": {"date": "2027-01-15", "status": "estimated"},
                   "camera_ready": {"date": "2027-03-01", "status": "estimated"}},
        "conference": {"start": "2027-05-10", "end": "2027-05-12", "status": "estimated"}}
TODAY = dt.date(2026, 10, 30)
def run(pages, conf=seed):
    S.fetch = lambda url: pages.get(url)
    return S.scrape_conference(copy.deepcopy(conf), TODAY)

# 1: all candidate pages in the past -> never pick an older edition
old = "Paper submission: October 1, 2025\nConference dates: May 11-13, 2026"
older = "Paper submission: October 1, 2023\nConference dates: May 11-13, 2024"
u, url = run({"https://2027.x.org/": None, "https://2026.x.org/": old, "https://x.org/cfp": older})
check("older editions ignored", (u, url), (seed, None))
# old deadline without conference dates is also ignored
u, url = run({"https://2026.x.org/": "Paper submission: October 1, 2025"})
check("old deadline-only page ignored", url, None)

# latest edition wins among candidates
cur = "Paper submission: November 5, 2026\nConference dates: May 17-20, 2027"
u, url = run({"https://2027.x.org/": cur, "https://2026.x.org/": old})
check("current edition picked", (url, u["stages"]["submission"], u["conference"]["start"]),
      ("https://2027.x.org/", {"date": "2026-11-05", "status": "confirmed"}, "2027-05-17"))

# 3: deadline-only page for the NEXT edition -> roll forward, shift the rest as estimated
nxt = "Paper submission: October 20, 2027"
u, url = run({"https://2027.x.org/": nxt})
check("deadline-only next edition", (u["edition_year"], u["stages"]["submission"], u["stages"]["notification"], u["conference"]),
      (2028, {"date": "2027-10-20", "status": "confirmed"}, {"date": "2028-01-15", "status": "estimated"},
       {"start": "2028-05-10", "end": "2028-05-12", "status": "estimated"}))
# page with full next edition
u, _ = run({"https://2027.x.org/": "Paper submission: Oct 20, 2027\nConference dates: May 15-18, 2028"})
check("full next edition", (u["edition_year"], u["stages"]["camera_ready"]["status"], u["conference"]["status"]),
      (2028, "estimated", "confirmed"))

# 4: no stage created where the seed has none
hipeac = {**copy.deepcopy(seed), "stages": {}}
u, _ = run({"https://2027.x.org/": "Paper submission: January 5, 2027\nConference dates: May 10-12, 2027"}, hipeac)
check("no new stages", u["stages"], {})

# inconsistent page: notification before submission is dropped
u, _ = run({"https://2027.x.org/": "Paper submission: November 5, 2026\nNotification: October 1, 2026\nConference dates: May 17-20, 2027"})
check("inconsistent notification dropped", u["stages"]["notification"], seed["stages"]["notification"])

# nothing parsed -> seed unchanged
u, url = run({})
check("no pages -> unchanged", (u, url), (seed, None))

# confirmed dates are protected; aggregator hosts skipped
conf2 = copy.deepcopy(seed); conf2["stages"]["submission"]["status"] = "confirmed"
u, _ = run({"https://2027.x.org/": "Paper submission: November 9, 2026\nAuthor notification: January 20, 2027"}, conf2)
check("confirmed kept", (u["stages"]["submission"], u["stages"]["notification"]["date"]),
      ({"date": "2026-11-01", "status": "confirmed"}, "2027-01-20"))
check("aggregator skipped", S._candidate_urls({"sources": ["https://www.myhuiban.com/conference/1", "https://x.org/"]}, 2026), ["https://x.org/"])
# two full dates joined into a range; word boundaries; inverted ranges
check("full-date range mdy", S._parse_range("May 17, 2027 - May 20, 2027"), ("2027-05-17", "2027-05-20"))
check("full-date range dmy", S._parse_range("17 May 2027 – 20 May 2027"), ("2027-05-17", "2027-05-20"))
check("full-date range iso", S._parse_range("2027-05-17 to 2027-05-20"), ("2027-05-17", "2027-05-20"))
check("no month inside a word", S._parse_single("Trojan 5, 2026"), None)
check("no day inside a number", S._parse_single("Room 123 June 2026"), None)
check("inverted range not trusted", S._parse_range("June 24-22, 2026"), ("2026-06-24", "2026-06-24"))

# a stray later-year range must not roll a confirmed entry forward
conf3 = copy.deepcopy(seed)
for st in conf3["stages"].values(): st["status"] = "confirmed"
conf3["conference"]["status"] = "confirmed"
u, _ = run({"https://2027.x.org/": "Registration: January 5-10, 2028\nPaper submission: November 5, 2026"}, conf3)
check("guessed range does not roll", (u["edition_year"], u["stages"]["notification"]), (2027, conf3["stages"]["notification"]))
u, _ = run({"https://2027.x.org/": "Paper submission: November 1, 2026\nConference dates: January 5-10, 2028"}, conf3)
check("roll needs newer deadline", u["edition_year"], 2027)
# a no-deadline entry can still roll on a labelled conference line
u, _ = run({"https://2027.x.org/": "Conference dates: January 17-19, 2028"}, hipeac)
check("no-deadline entry rolls", (u["edition_year"], u["conference"]["start"]), (2028, "2028-01-17"))
# missing edition_year falls back to the conference year
conf4 = copy.deepcopy(seed); del conf4["edition_year"]
u, _ = run({"https://2027.x.org/": "Paper submission: November 1, 2026\nAuthor notification: January 20, 2027"}, conf4)
check("missing edition_year keeps year", u.get("edition_year"), None)
check("missing edition_year", u["stages"]["notification"]["date"], "2027-01-20")

# --- regression tests for the review findings ---------------------------------
confirmed = copy.deepcopy(seed)
for st in confirmed["stages"].values(): st["status"] = "confirmed"
confirmed["conference"]["status"] = "confirmed"

# F1: a page whose newer-edition evidence is implausible is dropped entirely:
# no deadline-only reinterpretation, no partial merge
u, url = run({"https://2027.x.org/": "Paper submission: November 5, 2027\nConference dates: January 5-10, 2030"}, confirmed)
check("F1 implausible future page dropped", (u, url), (confirmed, None))
u, url = run({"https://2027.x.org/": "Author notification: January 20, 2028\nConference dates: May 15-18, 2028"})
check("F1 future page without deadline dropped", (u, url), (seed, None))
# a dropped page does not block a later good candidate
u, url = run({"https://2027.x.org/": "Paper submission: November 5, 2027\nConference dates: January 5-10, 2030",
              "https://2026.x.org/": cur})
check("F1 next candidate still used", (url, u["stages"]["submission"]["date"]), ("https://2026.x.org/", "2026-11-05"))
# next-edition notification on a page without a submission is out of window -> not merged
u, url = run({"https://2027.x.org/": "Author notification: January 20, 2028"})
check("F1 out-of-window notification ignored", (u["stages"]["notification"], url), (seed["stages"]["notification"], None))

# F2: deadline-only rolls go one edition at most, and only within one year of the seed's conference start
u, url = run({"https://2027.x.org/": "Paper submission: October 20, 2037"})
check("F2 far-future deadline rejected", (u["edition_year"], u["stages"]["submission"], url), (2027, seed["stages"]["submission"], None))
u, _ = run({"https://2027.x.org/": "Paper submission: October 20, 2028"})
check("F2 two editions ahead rejected", u["edition_year"], 2027)
u, _ = run({"https://2027.x.org/": "Paper submission: May 10, 2028"})   # exactly one year after conf start
check("F2 one-year bound inclusive", (u["edition_year"], u["stages"]["submission"]["date"]), (2028, "2028-05-10"))
u, _ = run({"https://2027.x.org/": "Paper submission: May 11, 2028"})
check("F2 one day past the bound rejected", u["edition_year"], 2027)
u, _ = run({"https://2027.x.org/": "Paper submission: May 10, 2027"})   # on the seed conference start: same edition
check("F2 deadline on conference start stays", u["edition_year"], 2027)
u, _ = run({"https://2027.x.org/": "Paper submission: Oct 20, 2028\nConference dates: May 15-18, 2029"})
check("F2 labelled range two years ahead rejected", u["edition_year"], 2027)
check("F2 _edition_of direct", [S._edition_of(seed, d, 2027) for d in (
      {"submission": "2027-10-20"}, {"submission": "2037-10-20"}, {"submission": "2026-11-05"},
      {"submission": "2027-10-20", "conference": ("2028-05-15", "2028-05-18")},
      {"submission": "2026-11-05", "conference": ("2028-05-15", "2028-05-18")},
      {"conference": ("2028-05-15", "2028-05-18")})], [2028, None, 2027, 2028, None, None])

# F3: table lookahead accepts only a bare date line or a same-stage line
check("F3 abstract line not taken", S.extract_important_dates(
      "Paper submission deadline\nAbstract registration: January 20, 2026\nMain track paper submission: January 27, 2026"),
      {"submission": "2026-01-27"})
check("F3 workshop line not taken", S.extract_important_dates(
      "Paper submission deadline\nWorkshop papers: May 1, 2026"), {})
check("F3 other milestone not taken", S.extract_important_dates(
      "Paper submission deadline\nNotification: March 28, 2026"), {"notification": "2026-03-28"})
check("F3 bare date line taken", S.extract_important_dates(
      "Paper submission deadline\nJanuary 27, 2026 (AoE)"), {"submission": "2026-01-27"})
check("F3 same-stage line taken", S.extract_important_dates(
      "Paper submission\nFull papers due: January 27, 2026"), {"submission": "2026-01-27"})
check("F3 registration line not taken", S.extract_important_dates(
      "Paper submission deadline\nRegistration opens: January 20, 2026"), {})
# a lookahead match does not block a later inline match; inline never overridden
check("F3 inline overrides lookahead", S.extract_important_dates(
      "Paper submission\nJanuary 20, 2026\nPaper submission: January 27, 2026"), {"submission": "2026-01-27"})
check("F3 first inline wins", S.extract_important_dates(
      "Paper submission: January 27, 2026\nPaper submission: February 3, 2026"), {"submission": "2026-01-27"})

# revised deadline split over two lines (<s>old</s> new flattens to two lines): last bare date line wins
check("F3 revised date on following bare line", S.extract_important_dates(
      "Paper submission\nJanuary 20, 2026\nJanuary 27, 2026 (extended)\nNotification\nMarch 28, 2026"),
      {"submission": "2026-01-27", "notification": "2026-03-28"})
check("F3 inline label, new date on next line", S.extract_important_dates(
      "Paper submission: January 20, 2026\nJanuary 27, 2026\nCamera-ready: April 15, 2026"),
      {"submission": "2026-01-27", "camera_ready": "2026-04-15"})
check("F3 following labelled line not swallowed", S.extract_important_dates(
      "Conference dates\nJune 22-24, 2026\nJune 25, 2026 (workshops)"), {"conference": ("2026-06-22", "2026-06-24")})
check("F3 html strike-through flattened", S.extract_important_dates(S.BeautifulSoup(
      "<tr><td>Paper submission deadline</td><td><s>Jan 20, 2026</s> <b>Jan 27, 2026</b></td></tr>"
      "<tr><td>Notification</td><td>Mar 28, 2026</td></tr>", "html.parser").get_text("\n")),
      {"submission": "2026-01-27", "notification": "2026-03-28"})

# F4: a later edition wins over an older one even if the older page's deadline is later in the calendar
pg27 = {"submission": "2027-01-05", "conference": ("2027-05-10", "2027-05-12")}
pg26 = {"submission": "2026-02-05", "conference": ("2026-05-10", "2026-05-12")}
check("F4 edition year before submission", (S._better(pg27, 2027, pg26, 2026), S._better(pg26, 2026, pg27, 2027)), (True, False))
check("F4 same edition: later deadline wins", S._better({"submission": "2027-01-12"}, 2027, pg27, 2027), True)
seed26 = copy.deepcopy(seed); seed26["edition_year"] = 2026
seed26["stages"]["submission"]["date"] = "2026-01-05"; seed26["conference"].update(start="2026-05-10", end="2026-05-12")
S.fetch = lambda url, p={"https://x.org/cfp": "Paper submission: February 5, 2026\nConference dates: May 10-12, 2026",
                         "https://x.org/": "Paper submission: January 5, 2027\nConference dates: May 10-12, 2027"}: p.get(url)
u, url = S.scrape_conference({**copy.deepcopy(seed26), "url_template": None, "sources": ["https://x.org/cfp", "https://x.org/"]}, dt.date(2027, 6, 1))
check("F4 newer edition page wins when all past", (url, u["edition_year"]), ("https://x.org/", 2027))

# F5: alphanumeric boundaries around whole date tokens
check("F5 glued prefix dmy", S._parse_single("abc27 January 2026"), None)
check("F5 glued prefix mdy", S._parse_single("123May 5, 2026"), None)
check("F5 glued iso", S._parse_single("abc2026-01-27def"), None)
check("F5 glued suffix", S._parse_single("January 27, 2026abc"), None)
check("F5 punctuation ok", S._parse_single("(January 27, 2026)."), "2026-01-27")
check("F5 colon glued ok", S._parse_single("Deadline:2026-01-27"), "2026-01-27")
check("F5 ordinal and range ok", S._parse_range("22nd–24th June 2026!"), ("2026-06-22", "2026-06-24"))
check("F5 time after date ok", S._parse_single("27 January 2026, 23:59 AoE"), "2026-01-27")

# F6: aggregator hosts: case-insensitive, subdomains and dblp mirrors, hostname only
check("F6 aggregator hosts", [S._is_aggregator(u) for u in (
      "https://DBLP.ORG/db/conf/x", "https://dblp.uni-trier.de/db/conf/x", "https://dblp.dagstuhl.de/x",
      "http://www.wikicfp.com/cfp/", "https://x.org/?ref=dblp.org", "https://notdblp.org/", "https://x.org/")],
      [True, True, True, True, False, False, False])
check("F6 candidate urls", S._candidate_urls({"sources": ["https://DBLP.ORG/x", "https://x.org/"]}, 2026), ["https://x.org/"])

# F7: a guessed range is never written as the conference dates
u, url = run({"https://2027.x.org/": "Registration: January 5-10, 2027\nPaper submission: November 5, 2026"})
check("F7 guessed range not written", (u["conference"], u["stages"]["submission"]["date"]), (seed["conference"], "2026-11-05"))
u, url = run({"https://2027.x.org/": "Registration: January 5-10, 2027"})
check("F7 guessed-only page ignored", (u, url), (seed, None))
# ... a guessed range that contradicts the page's own deadline is dropped and the deadline used
u, url = run({"https://2027.x.org/": "Registration: January 5-10, 2026\nPaper submission: November 5, 2026"})
check("F7 inconsistent guessed range dropped", (u["edition_year"], u["stages"]["submission"]["date"], u["conference"]), (2027, "2026-11-05", seed["conference"]))
# ... and a guessed range on a no-deadline entry is only an edition hint, never written
u, url = run({"https://2027.x.org/": "Registration: January 5-10, 2026"}, hipeac)
check("F7 guessed old range -> page ignored", url, None)
u, url = run({"https://2027.x.org/": "Registration: January 5-10, 2028"}, hipeac)
check("F7 guessed newer range -> page ignored", (u, url), (hipeac, None))

# self-review probes: edition window, cross-year editions, same-edition moves
u, _ = run({"https://2027.x.org/": "Paper submission: November 5, 2026\nCamera-ready: June 1, 2027\nConference dates: May 17-20, 2027"})
check("SR camera-ready after conference dropped", u["stages"]["camera_ready"], seed["stages"]["camera_ready"])
u, _ = run({"https://2027.x.org/": "Paper submission: June 1, 2027\nConference dates: September 17-20, 2027"})
check("SR same-year move of the conference", (u["edition_year"], u["stages"]["submission"]["date"], u["conference"]["start"]),
      (2027, "2027-06-01", "2027-09-17"))
xyear = {**copy.deepcopy(seed), "stages": {"submission": {"date": "2026-07-01", "status": "estimated"}},
         "conference": {"start": "2026-12-30", "end": "2027-01-02", "status": "estimated"}}
u, url = run({"https://2027.x.org/": "Paper submission: July 1, 2026\nConference dates: December 29-31, 2026"}, xyear)
check("SR cross-year edition is not 'older'", (u["edition_year"], u["conference"]["start"], url), (2027, "2026-12-29", "https://2027.x.org/"))
u, _ = run({"https://2027.x.org/": "Paper submission: July 1, 2027\nConference dates: December 29, 2027 - January 1, 2028"}, xyear)
check("SR cross-year edition rolls by one", (u["edition_year"], u["conference"]), (2028, {"start": "2027-12-29", "end": "2028-01-01", "status": "confirmed"}))
u, _ = run({"https://2027.x.org/": "Paper submission: October 20, 2026", "https://2026.x.org/": "Paper submission: October 27, 2026"})
check("SR same edition, later (revised) deadline wins", u["stages"]["submission"]["date"], "2026-10-27")
nosub = {**copy.deepcopy(seed), "stages": {"notification": {"date": "2027-01-15", "status": "estimated"}}}
u, url = run({"https://2027.x.org/": "Author notification: January 20, 2028"}, nosub)
check("SR no-deadline entry: next-year notification alone ignored", (u, url), (nosub, None))
u, _ = run({"https://2027.x.org/": "Author notification: January 20, 2028\nConference dates: May 15-18, 2028"}, nosub)
check("SR no-deadline entry rolls with labelled range", (u["edition_year"], u["stages"]["notification"]), (2028, {"date": "2028-01-20", "status": "confirmed"}))
check("SR edition_year follows the shift", run({"https://2027.x.org/": "Paper submission: Oct 20, 2027\nConference dates: May 15-18, 2028"},
      {**copy.deepcopy(seed), "edition_year": 2026})[0]["edition_year"], 2027)

# fetch(): UTF-8 page without a charset header keeps its en dash
class _Raw:
    def __init__(self, b): self.b, self.pos = b, 0
    def read(self, n, decode_content=True):
        out = self.b[self.pos:self.pos + n]; self.pos += len(out); return out
class _Resp:
    def __init__(self, b, ctype): self.raw, self.headers, self.encoding = _Raw(b), {"content-type": ctype}, "ISO-8859-1"
    def raise_for_status(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
import importlib; importlib.reload(S)
S.requests.get = lambda *a, **k: _Resp('<p>Conference dates: June 22–24, 2026</p>'.encode(), "text/html")
check("utf-8 without charset", S.extract_important_dates(S.fetch("https://x.org/")).get("conference"), ("2026-06-22", "2026-06-24"))
S.requests.get = lambda *a, **k: _Resp(b"x" * (S.MAX_BYTES + 10), "text/html")
check("oversized page skipped", S.fetch("https://x.org/"), None)
S.requests.get = lambda *a, **k: _Resp(b"<p>Paper submission: May 1, 2026</p>" * 5000, "text/html")
check("multi-chunk page read whole", S.extract_important_dates(S.fetch("https://x.org/")).get("submission"), "2026-05-01")
S.TOTAL_TIMEOUT = -1
S.requests.get = lambda *a, **k: _Resp(b"<p>slow</p>", "text/html")
check("slow page gives up", S.fetch("https://x.org/"), None)
S.TOTAL_TIMEOUT = 45

# refresh.py: layout round trip, and nothing written when too few pages parse
import json, tempfile, importlib.util
spec = importlib.util.spec_from_file_location("refresh", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "refresh.py"))
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
check("layout round trip", R.format_data(R.load_data()), open(R.DATA_FILE, encoding="utf-8").read())
with tempfile.TemporaryDirectory() as tmp:
    R.DATA_FILE = R.Path(tmp) / "c.json"
    R.save_data({"last_updated": "2026-01-01", "conferences": [copy.deepcopy(seed)]})
    before = R.DATA_FILE.read_text()
    changed = copy.deepcopy(seed); changed["stages"]["notification"]["date"] = "2027-01-20"
    R.scrapers.scrape_all = lambda data: ({**data, "conferences": [changed]}, {"x": "https://x.org/"})
    r = R.refresh(write=True, fail_under=5)
    check("fail-under writes nothing", (r["parsed"], R.DATA_FILE.read_text() == before), (1, True))
    R.refresh(write=True, fail_under=1)
    written = json.loads(R.DATA_FILE.read_text())
    check("normal run writes", written["conferences"][0]["stages"]["notification"]["date"], "2027-01-20")
    check("date change bumps last_updated", written["last_updated"], dt.date.today().isoformat())
    # F8: a status-only change is saved but does not touch last_updated
    R.save_data({"last_updated": "2026-01-01", "conferences": [copy.deepcopy(seed)]})
    status_only = copy.deepcopy(seed); status_only["stages"]["submission"]["status"] = "confirmed"
    R.scrapers.scrape_all = lambda data: ({**data, "conferences": [status_only]}, {"x": "https://x.org/"})
    r = R.refresh(write=True, fail_under=1)
    written = json.loads(R.DATA_FILE.read_text())
    check("F8 status-only change saved, last_updated kept",
          (r["changed"], r["dates_changed"], written["last_updated"], written["conferences"][0]["stages"]["submission"]["status"]),
          (True, False, "2026-01-01", "confirmed"))
    # no-op run: byte-for-byte identical
    before = R.DATA_FILE.read_text()
    R.scrapers.scrape_all = lambda data: ({**data, "conferences": copy.deepcopy(data["conferences"])}, {"x": "https://x.org/"})
    r = R.refresh(write=True, fail_under=1)
    check("no-op run leaves file identical", (r["changed"], R.DATA_FILE.read_text() == before), (False, True))

# numeric dates: dotted = day-first; slashes only when unambiguous or the page shows its convention
check("N dotted", S._parse_single("Paper submission: 27.01.2026"), "2026-01-27")
check("N dotted short", S._parse_single("Deadline 7.1.2026"), "2026-01-07")
check("N dotted 2-digit year", S._parse_single("Deadline: 27.01.26"), "2026-01-27")
check("N dotted range same month", S._parse_range("Conference: 22.-24.06.2026"), ("2026-06-22", "2026-06-24"))
check("N dotted range en dash", S._parse_range("Conference: 22.–24.06.2026"), ("2026-06-22", "2026-06-24"))
check("N dotted range two months", S._parse_range("Conference: 29.06.–02.07.2026"), ("2026-06-29", "2026-07-02"))
check("N dotted two full dates", S._parse_range("Conference: 22.06.2026 - 24.06.2026"), ("2026-06-22", "2026-06-24"))
check("N dotted invalid", S._parse_single("Deadline: 31.02.2026"), None)
check("N version number", S._parse_single("Release v2.1.2026 is out"), None)
check("N ip-like", S._parse_single("Server 10.1.12.2026 down"), None)
check("N dotted followed by digit", S._parse_single("Section 1.2.2026.5"), None)
check("N slash day-first unambiguous", S._parse_single("Deadline: 27/01/2026"), "2026-01-27")
check("N slash month-first unambiguous", S._parse_single("Deadline: 01/27/2026"), "2026-01-27")
check("N slash same day/month", S._parse_single("Deadline: 05/05/2026"), "2026-05-05")
check("N slash ambiguous alone", S._parse_single("Deadline: 03/04/2026"), None)
check("N slash in URL path", S._parse_single("see /2026/03/04/cfp"), None)
check("N slash fraction", S._parse_single("ratio 1/2/2026x"), None)
check("N iso range with slash", S._parse_range("Conference: 2027-05-17/2027-05-20"), ("2027-05-17", "2027-05-20"))
check("N convention from unambiguous slash", S.extract_important_dates(
      "Paper submission: 03/04/2026\nCamera-ready: 25/05/2026").get("submission"), "2026-04-03")
check("N convention month-first", S.extract_important_dates(
      "Paper submission: 03/04/2026\nCamera-ready: 05/25/2026").get("submission"), "2026-03-04")
check("N convention from named dates (dmy)", S.extract_important_dates(
      "Paper submission: 03/04/2026\nConference dates: 22-24 June 2026").get("submission"), "2026-04-03")
check("N convention from named dates (mdy)", S.extract_important_dates(
      "Paper submission: 03/04/2026\nConference dates: June 22-24, 2026").get("submission"), "2026-03-04")
check("N convention from dotted dates", S.extract_important_dates(
      "Paper submission: 03/04/2026\nCamera-ready: 25.05.2026").get("submission"), "2026-04-03")
check("N mixed signals stay unread", S.extract_important_dates(
      "Paper submission: 03/04/2026\nNotification: 25/05/2026\nCamera-ready: 06/30/2026").get("submission"), None)
check("N no signal stays unread", S.extract_important_dates("Paper submission: 03/04/2026").get("submission"), None)
check("N convention does not leak", S._parse_single("Deadline: 03/04/2026"), None)
check("N full page dotted", S.extract_important_dates(
      "Wichtige Termine\nPaper submission: 15.12.2026\nAuthor notification: 08.02.2027\nCamera-ready: 24.02.2027\nConference dates: 09.-11.03.2027"),
      {"submission": "2026-12-15", "notification": "2027-02-08", "camera_ready": "2027-02-24", "conference": ("2027-03-09", "2027-03-11")})

# refresh.py: --list tolerates entries without edition_year/location; main() exit codes
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    R.print_list({"conferences": [{"name": "Y", "stages": {}}, {"name": "Z", "edition_year": None, "location": None}]})
check("print_list without edition_year", [l.split()[0] for l in buf.getvalue().splitlines()], ["Y", "Z"])
with tempfile.TemporaryDirectory() as tmp:
    R.DATA_FILE = R.Path(tmp) / "c.json"
    R.save_data({"last_updated": "2026-01-01", "conferences": [copy.deepcopy(seed)]})
    R.scrapers.scrape_all = lambda data: ({**data, "conferences": copy.deepcopy(data["conferences"])}, {"x": None})
    argv = sys.argv
    with contextlib.redirect_stdout(io.StringIO()):
        sys.argv = ["refresh.py", "--fail-under", "1"]; code_fail = R.main()
        R.scrapers.scrape_all = lambda data: ({**data, "conferences": copy.deepcopy(data["conferences"])}, {"x": "https://x.org/"})
        code_ok = R.main()
        sys.argv = ["refresh.py", "--list"]; code_list = R.main()
    sys.argv = argv
    check("main exit codes (fail-under, ok, --list)", (code_fail, code_ok, code_list), (1, 0, 0))

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
