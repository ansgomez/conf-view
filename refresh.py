"""Offline data-refresh tool for ConfView.

The site is static, so nothing scrapes in the background. Run this script (by
hand, or from the weekly GitHub Actions workflow) to pull fresh dates from the
official CFP pages into data/conferences.json, then rebuild the Hugo site.

    python refresh.py            # scrape all sites, rewrite data/conferences.json
    python refresh.py --list     # just print what's currently in the data file
    python refresh.py --dry-run  # scrape but print a summary without writing

    python refresh.py --fail-under 5   # exit 1 if fewer than 5 pages parsed

Environment:
    CONFVIEW_DATA   path to the data file (default: data/conferences.json next
                    to this script)

The scraping logic lives in scrapers.py. Parsed dates are marked "confirmed";
anything that can't be found keeps its seed value, so a failed fetch never
blanks the data. The file is written in the same compact layout it is edited
in, and `last_updated` only changes when a date value did (a status-only
change, e.g. estimated -> confirmed on the same date, is saved without bumping
it), so a run that finds nothing new leaves the file byte-for-byte unchanged
(no diff, no PR).

Exit status is 1, and nothing is written, if fewer than --fail-under pages
(default 1) could be parsed, e.g. with no network, so a broken run never
changes the file or opens a PR.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
from pathlib import Path

import scrapers

DATA_FILE = Path(os.environ.get("CONFVIEW_DATA",
                               Path(__file__).resolve().parent / "data" / "conferences.json"))


def load_data() -> dict:
    with open(DATA_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def _dated(v: dict) -> str:
    """One-line form of a stage or conference object: { "date": ..., ... }."""
    return "{ " + ", ".join(f"{_j(k)}: {_j(x)}" for k, x in v.items()) + " }"


def format_data(data: dict) -> str:
    """Serialise in the hand-edited layout of data/conferences.json: one key per
    line per conference, lists and stage objects on one line, stage names
    aligned."""
    out = ["{"]
    for k, v in data.items():
        if k != "conferences":
            out.append(f"  {_j(k)}: {_j(v)},")
    out.append('  "conferences": [')
    confs = data.get("conferences", [])
    for i, c in enumerate(confs):
        out.append("    {")
        items = list(c.items())
        for n, (k, v) in enumerate(items):
            comma = "," if n < len(items) - 1 else ""
            if k == "stages":
                if not v:
                    out.append(f'      "stages": {{}}{comma}')
                    continue
                out.append('      "stages": {')
                stages = list(v.items())
                for m, (sk, sv) in enumerate(stages):
                    key = f"{_j(sk)}:".ljust(15)
                    out.append(f"        {key} {_dated(sv)}" + ("," if m < len(stages) - 1 else ""))
                out.append("      }" + comma)
            elif k == "conference":
                out.append(f"      {_j(k)}: {_dated(v)}{comma}")
            else:
                out.append(f"      {_j(k)}: {_j(v)}{comma}")
        out.append("    }" + ("," if i < len(confs) - 1 else ""))
    out.append("  ]")
    out.append("}")
    return "\n".join(out) + "\n"


def save_data(data: dict) -> None:
    tmp = DATA_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(format_data(data))
    tmp.replace(DATA_FILE)


def _submission_date(conf: dict):
    d = conf.get("stages", {}).get("submission", {}).get("date")
    try:
        return _dt.date.fromisoformat(d) if d else None
    except ValueError:
        return None


def print_list(data: dict) -> None:
    rows = sorted(
        data.get("conferences", []),
        key=lambda c: (_submission_date(c) or _dt.date.max),
    )
    if not rows:
        print("(none)")
        return
    for c in rows:
        sub = c.get("stages", {}).get("submission", {})
        print(f"{c['name']:<10} {c['edition_year']:<6} {c.get('location',''):<28} "
              f"submit {sub.get('date','?')} [{sub.get('status','?')}]")


def _date_values(confs: list) -> list:
    """The date-bearing fields of every conference (stage dates, conference
    start/end, edition year), without statuses, in file order."""
    return [(c.get("id"), c.get("edition_year"),
             {k: v.get("date") for k, v in c.get("stages", {}).items()},
             c.get("conference", {}).get("start"), c.get("conference", {}).get("end"))
            for c in confs]


def refresh(write: bool = True, fail_under: int = 1) -> dict:
    """Scrape, then write the data file if anything changed and enough pages
    parsed. ``last_updated`` is bumped only when a date value changed; a run
    that only flips a status (estimated -> confirmed on an unchanged date) is
    still saved, but keeps the old ``last_updated``."""
    data = load_data()
    fresh, sources = scrapers.scrape_all(data)
    ok = sum(1 for url in sources.values() if url)
    total = len(fresh["conferences"])
    changed = fresh["conferences"] != data.get("conferences")
    dates_changed = _date_values(fresh["conferences"]) != _date_values(data.get("conferences", []))
    if dates_changed:
        fresh["last_updated"] = _dt.date.today().isoformat()
    for cid, url in sources.items():
        print(f"  {cid:<12} {url or '(no page parsed, kept seed)'}")
    what = "dates changed" if dates_changed else "only statuses changed"
    if ok < fail_under:
        print(f"parsed {ok}/{total} pages, fewer than {fail_under}: nothing written")
    elif not changed:
        print(f"parsed {ok}/{total} pages, nothing changed")
    elif write:
        save_data(fresh)
        print(f"parsed {ok}/{total} pages, {what} -> {DATA_FILE}")
    else:
        print(f"parsed {ok}/{total} pages, {what} (dry run, not written)")
    print_list(fresh)
    return {"parsed": ok, "total": total, "changed": changed, "dates_changed": dates_changed}


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh ConfView conference data.")
    ap.add_argument("--list", action="store_true", help="print current data and exit")
    ap.add_argument("--dry-run", action="store_true", help="scrape but do not write")
    ap.add_argument("--fail-under", type=int, default=1, metavar="N",
                    help="exit 1 if fewer than N pages parsed (default 1)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.list:
        print_list(load_data())
        return 0
    result = refresh(write=not args.dry_run, fail_under=args.fail_under)
    return 0 if result["parsed"] >= args.fail_under else 1


if __name__ == "__main__":
    raise SystemExit(main())
