#!/usr/bin/env python3
"""Collect GitHub activity for time-boxed SasView events (camps, hackathons, sprints).

Usage:
  python collector/collect.py                 # build site/data for every event in events/
  python collector/collect.py --check-active  # print "true"/"false": is any event running now?
  python collector/collect.py --archive ID    # write final numbers for one event to archive/ID/

Reads events/*.toml. For an event with a folder in archive/, the archived numbers are
copied instead of re-fetched. Set DASHBOARD_TOKEN (a GitHub token) to raise API limits.
"""
import argparse, glob, json, os, re, shutil, statistics, sys, tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS_DIR = os.path.join(ROOT, "events")
ARCHIVE_DIR = os.path.join(ROOT, "archive")
SITE_DATA = os.path.join(ROOT, "site", "data")
AI_RE = re.compile(r"co-authored-by:.*(claude|copilot|cursor|codex|gemini|anthropic|openai)", re.I)
DATASETS = ["meta", "summary", "daily", "by_repo", "pr_closures", "review_queue", "first_timers"]

S = requests.Session()
S.headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
if os.environ.get("DASHBOARD_TOKEN"):
    S.headers["Authorization"] = "Bearer " + os.environ["DASHBOARD_TOKEN"]


# ---------------------------------------------------------------- config
def load_events():
    events = []
    for path in sorted(glob.glob(os.path.join(EVENTS_DIR, "*.toml"))):
        with open(path, "rb") as f:
            ev = tomllib.load(f)
        for key in ("id", "title", "start", "end", "timezone", "repos"):
            if key not in ev:
                sys.exit(f"{path}: missing required key '{key}'")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", ev["id"]):
            sys.exit(f"{path}: id may only contain letters, digits, '-' and '_'")
        tz = ZoneInfo(ev["timezone"])
        s, e = date.fromisoformat(str(ev["start"])), date.fromisoformat(str(ev["end"]))
        ev["_tz"] = tz
        ev["_start"] = datetime(s.year, s.month, s.day, tzinfo=tz)
        ev["_end"] = datetime(e.year, e.month, e.day, tzinfo=tz) + timedelta(days=1)  # exclusive
        ev["_days"] = [s + timedelta(days=i) for i in range((e - s).days + 1)]
        ev.setdefault("subtitle", "")
        ev.setdefault("link", "")
        ev.setdefault("baseline_weeks", 8)
        events.append(ev)
    return events


def status(ev, now):
    if os.path.isdir(os.path.join(ARCHIVE_DIR, ev["id"])):
        return "archived"
    if now < ev["_start"]:
        return "upcoming"
    if now < ev["_end"]:
        return "live"
    return "ended"


# ---------------------------------------------------------------- GitHub helpers
def get(url, params=None):
    r = S.get(url if url.startswith("http") else "https://api.github.com" + url, params=params, timeout=60)
    r.raise_for_status()
    return r


def paged(url, params=None):
    params = dict(params or {}, per_page=100)
    out, page = [], 1
    while True:
        params["page"] = page
        r = get(url, params)
        batch = r.json()
        out.extend(batch)
        if "next" not in r.links or not batch:
            break
        page += 1
    return out


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def is_bot(user):
    if not user:
        return True
    return user.get("type") == "Bot" or user.get("login", "").endswith("[bot]")


# ---------------------------------------------------------------- metrics
def collect(ev):
    START, END, TZ, REPOS = ev["_start"], ev["_end"], ev["_tz"], ev["repos"]
    now = datetime.now(timezone.utc)
    win_end = min(now, END)
    base_weeks = ev["baseline_weeks"]
    base_start = START - timedelta(weeks=base_weeks)

    def in_win(t):
        return t is not None and START <= t < END

    def day(t):
        return t.astimezone(TZ).date().isoformat()

    prs_all, issues_win, commits_main = {}, {}, {}
    for repo in REPOS:
        prs_all[repo] = paged(f"/repos/{repo}/pulls", {"state": "all", "sort": "created", "direction": "desc"})
        issues_win[repo] = [i for i in paged(f"/repos/{repo}/issues",
                                             {"state": "all", "since": START.astimezone(timezone.utc).isoformat()})
                            if "pull_request" not in i]
        info = get(f"/repos/{repo}").json()
        commits_main[repo] = paged(f"/repos/{repo}/commits",
                                   {"sha": info["default_branch"],
                                    "since": (START - timedelta(days=2)).astimezone(timezone.utc).isoformat(),
                                    "until": (END + timedelta(days=2)).astimezone(timezone.utc).isoformat()})

    camp_prs = []
    for repo in REPOS:
        for p in prs_all[repo]:
            if is_bot(p["user"]):
                continue
            c, cl, up = ts(p["created_at"]), ts(p["closed_at"]), ts(p["updated_at"])
            if in_win(c) or in_win(cl) or (p["state"] == "open" and up >= START and c < END):
                camp_prs.append((repo, p))

    def detail(rp):
        repo, p = rp
        return (repo, p, paged(f"/repos/{repo}/pulls/{p['number']}/commits"),
                paged(f"/repos/{repo}/pulls/{p['number']}/reviews"))

    with ThreadPoolExecutor(8) as ex:
        details = list(ex.map(detail, camp_prs))

    def closer(ri):
        repo, i = ri
        cl = ts(i["closed_at"])
        for e in paged(f"/repos/{repo}/issues/{i['number']}/timeline"):
            if e.get("event") == "closed" and e.get("commit_id"):
                return repo, i, True
            if e.get("event") == "cross-referenced":
                src = (e.get("source") or {}).get("issue") or {}
                m = ts((src.get("pull_request") or {}).get("merged_at"))
                if m and abs((cl - m).total_seconds()) < 600:
                    return repo, i, True
        return repo, i, False

    with ThreadPoolExecutor(8) as ex:
        closers = list(ex.map(closer, [(r, i) for r in REPOS for i in issues_win[r] if in_win(ts(i["closed_at"]))]))

    pr_rows, commit_set, people, review_events = [], {}, set(), []
    for repo, p, commits, reviews in details:
        c, cl, m = ts(p["created_at"]), ts(p["closed_at"]), ts(p["merged_at"])
        new = in_win(c)
        ev_commits = [x for x in commits if in_win(ts(x["commit"]["author"]["date"]))
                      and len(x.get("parents", [])) < 2 and not is_bot(x.get("author"))]
        for x in ev_commits:
            commit_set[x["sha"]] = (repo, ts(x["commit"]["author"]["date"]),
                                    (x.get("author") or {}).get("login") or x["commit"]["author"]["name"])
        ai = any(AI_RE.search(x["commit"]["message"]) for x in ev_commits) or bool(AI_RE.search(p.get("body") or ""))
        hum = [r for r in reviews if r.get("submitted_at") and not is_bot(r.get("user"))
               and (r.get("user") or {}).get("login") != p["user"]["login"]]
        for r in hum:
            if in_win(ts(r["submitted_at"])):
                review_events.append((repo, ts(r["submitted_at"]), r["user"]["login"]))
                people.add(r["user"]["login"])
        first_rev = min((ts(r["submitted_at"]) for r in hum), default=None)
        cat = None
        if in_win(cl):
            if new:
                cat = "New work, merged" if m else "New, closed unmerged"
            elif m:
                cat = "Older PR reworked, merged" if ev_commits else "Backlog PR merged as-is"
            else:
                cat = "Older PR closed (cleanup)"
        if new:
            people.add(p["user"]["login"])
        pr_rows.append(dict(repo=repo, number=p["number"], title=p["title"], author=p["user"]["login"],
                            created=c, closed=cl, merged=m, new=new, draft=p.get("draft", False),
                            state=p["state"], category=cat, ev_commits=len(ev_commits), ai=ai,
                            first_review=first_rev))

    for repo in REPOS:
        for x in commits_main[repo]:
            t = ts(x["commit"]["author"]["date"])
            if in_win(t) and len(x.get("parents", [])) < 2 and not is_bot(x.get("author")):
                commit_set.setdefault(x["sha"], (repo, t, (x.get("author") or {}).get("login") or x["commit"]["author"]["name"]))
    people.update(who for _, _, who in commit_set.values())

    closer_map = {(r, i["number"]): b for r, i, b in closers}
    issue_rows = []
    for r in REPOS:
        for i in issues_win[r]:
            c, cl = ts(i["created_at"]), ts(i["closed_at"])
            opened = in_win(c) and not is_bot(i["user"])
            if opened:
                people.add(i["user"]["login"])
            issue_rows.append(dict(repo=r, created=c, closed=cl, opened_in=opened, closed_in=in_win(cl),
                                   by_pr=closer_map.get((r, i["number"]))))

    first_pr = {}
    for repo in REPOS:
        for p in prs_all[repo]:
            if is_bot(p["user"]):
                continue
            lg, c = p["user"]["login"], ts(p["created_at"])
            if lg not in first_pr or c < first_pr[lg][0]:
                first_pr[lg] = (c, repo, p)
    first_timers = [dict(contributor=lg, repo=repo.split("/")[1], pr=f"{repo.split('/')[1]}#{p['number']}",
                         url=p["html_url"], title=p["title"], opened=day(c),
                         status="merged" if p.get("merged_at") else p["state"])
                    for lg, (c, repo, p) in sorted(first_pr.items(), key=lambda kv: kv[1][0]) if in_win(c)]

    def human(p):
        return not is_bot(p["user"])
    base_open = sum(1 for r in REPOS for p in prs_all[r] if human(p) and base_start <= ts(p["created_at"]) < START)
    base_merged = sum(1 for r in REPOS for p in prs_all[r] if human(p) and p.get("merged_at") and base_start <= ts(p["merged_at"]) < START)

    cats = [x["category"] for x in pr_rows if x["category"]]
    awaiting = [x for x in pr_rows if x["state"] == "open" and not x["draft"] and x["first_review"] is None
                and (x["new"] or x["ev_commits"])]
    ttfr = [(x["first_review"] - x["created"]).total_seconds() / 3600 for x in pr_rows if x["new"] and x["first_review"]]
    iss_closed = [x for x in issue_rows if x["closed_in"]]
    elapsed_days = max((win_end - START).total_seconds() / 86400, 0.01)
    merged = sum(1 for x in pr_rows if in_win(x["merged"]))

    summary = dict(
        prs_opened=sum(x["new"] for x in pr_rows), prs_merged=merged, prs_closed=len(cats),
        issues_opened=sum(x["opened_in"] for x in issue_rows), issues_closed=len(iss_closed),
        issues_closed_by_pr=sum(1 for x in iss_closed if x["by_pr"]),
        issues_closed_by_hand=sum(1 for x in iss_closed if not x["by_pr"]),
        commits=len(commit_set), contributors=len(people), first_timers=len(first_timers),
        reviews=len(review_events), awaiting_review=len(awaiting),
        median_hours_to_first_review=round(statistics.median(ttfr), 1) if ttfr else None,
        ai_assisted_prs=sum(1 for x in pr_rows if x["ai"] and (x["new"] or x["ev_commits"])),
        baseline_weeks=base_weeks,
        baseline_prs_opened_per_week=round(base_open / base_weeks, 1),
        baseline_prs_merged_per_week=round(base_merged / base_weeks, 1),
        prs_merged_per_day=round(merged / elapsed_days, 1),
    )

    daily, cum = [], dict(prs_opened=0, prs_merged=0, issues_closed=0, commits=0, reviews=0)
    for d in ev["_days"]:
        if datetime(d.year, d.month, d.day, tzinfo=TZ) > now:
            break
        ds = d.isoformat()
        row = dict(day=ds,
                   prs_opened=sum(1 for x in pr_rows if x["new"] and day(x["created"]) == ds),
                   prs_merged=sum(1 for x in pr_rows if in_win(x["merged"]) and day(x["merged"]) == ds),
                   issues_opened=sum(1 for x in issue_rows if x["opened_in"] and day(x["created"]) == ds),
                   issues_closed=sum(1 for x in issue_rows if x["closed_in"] and day(x["closed"]) == ds),
                   commits=sum(1 for _, t, _ in commit_set.values() if day(t) == ds),
                   reviews=sum(1 for _, t, _ in review_events if day(t) == ds))
        for k in cum:
            cum[k] += row[k]
            row["cum_" + k] = cum[k]
        daily.append(row)

    by_repo = [dict(repo=r.split("/")[1],
                    prs_opened=sum(1 for x in pr_rows if x["repo"] == r and x["new"]),
                    prs_merged=sum(1 for x in pr_rows if x["repo"] == r and in_win(x["merged"])),
                    issues_opened=sum(1 for x in issue_rows if x["repo"] == r and x["opened_in"]),
                    issues_closed=sum(1 for x in issue_rows if x["repo"] == r and x["closed_in"]),
                    commits=sum(1 for v in commit_set.values() if v[0] == r),
                    reviews=sum(1 for v in review_events if v[0] == r)) for r in REPOS]

    order = ["New work, merged", "Older PR reworked, merged", "Backlog PR merged as-is",
             "Older PR closed (cleanup)", "New, closed unmerged"]
    pr_closures = [dict(category=c, prs=cats.count(c)) for c in order]

    review_queue = sorted([dict(pr=f"{x['repo'].split('/')[1]}#{x['number']}",
                                url=f"https://github.com/{x['repo']}/pull/{x['number']}",
                                title=x["title"], author=x["author"],
                                opened=x["created"].astimezone(TZ).strftime("%a %d %b %H:%M"),
                                hours_waiting=round((now - x["created"]).total_seconds() / 3600, 1))
                           for x in awaiting], key=lambda r: -r["hours_waiting"])

    meta = dict(id=ev["id"], title=ev["title"], subtitle=ev["subtitle"], link=ev["link"],
                start=ev["_days"][0].isoformat(), end=ev["_days"][-1].isoformat(), timezone=ev["timezone"],
                days=[d.isoformat() for d in ev["_days"]], repos=REPOS,
                generated_at=now.isoformat(timespec="seconds"),
                generated_local=now.astimezone(TZ).strftime("%a %d %b %H:%M"),
                final=now >= END)
    return dict(meta=meta, summary=summary, daily=daily, by_repo=by_repo, pr_closures=pr_closures,
                review_queue=review_queue, first_timers=first_timers)


def write(dirpath, data):
    os.makedirs(dirpath, exist_ok=True)
    for k in DATASETS:
        with open(os.path.join(dirpath, f"{k}.json"), "w") as f:
            json.dump(data[k], f, indent=1, default=str)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-active", action="store_true")
    ap.add_argument("--archive", metavar="ID")
    args = ap.parse_args()
    events = load_events()
    now = datetime.now(timezone.utc)

    if args.check_active:
        # active = running now, or within a day either side (so the last day gets its final numbers)
        print("true" if any(ev["_start"] - timedelta(days=1) <= now < ev["_end"] + timedelta(days=1)
                            for ev in events) else "false")
        return

    if args.archive:
        ev = next((e for e in events if e["id"] == args.archive), None)
        if not ev:
            sys.exit(f"no event with id {args.archive}")
        write(os.path.join(ARCHIVE_DIR, ev["id"]), collect(ev))
        print(f"archived {ev['id']}")
        return

    if os.path.isdir(SITE_DATA):
        shutil.rmtree(SITE_DATA)
    index = []
    for ev in events:
        st = status(ev, now)
        out = os.path.join(SITE_DATA, ev["id"])
        if st == "archived":
            shutil.copytree(os.path.join(ARCHIVE_DIR, ev["id"]), out)
        elif st != "upcoming":
            print(f"collecting {ev['id']} ...", flush=True)
            write(out, collect(ev))
        index.append(dict(id=ev["id"], title=ev["title"], start=ev["_days"][0].isoformat(),
                          end=ev["_days"][-1].isoformat(), status=st))
    index.sort(key=lambda e: e["start"], reverse=True)
    os.makedirs(SITE_DATA, exist_ok=True)
    with open(os.path.join(SITE_DATA, "events.json"), "w") as f:
        json.dump(index, f, indent=1)
    print(json.dumps(index, indent=1))


if __name__ == "__main__":
    main()
