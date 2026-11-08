"""Before/after and status metrics for an event: tracked branches, backlog, CI, tests and
docstring coverage, and unreleased work. Used by collect.py."""
import os, re, statistics, subprocess, sys
from datetime import timedelta, timezone

DEFAULT_EXCLUDE = r"^release"


# ---------------------------------------------------------------- branches
def tracked_branches(get, paged, repo, default_branch, ev):
    """Branches to measure: the repo's setting if given, otherwise protected branches whose
    name doesn't match exclude_branches (default: anything starting 'release') and that had
    commits during the event. The default branch is always included."""
    settings = ev["repo"].get(repo, {})
    if settings.get("branches"):
        return list(dict.fromkeys([default_branch] + settings["branches"]))
    exclude = re.compile(ev.get("exclude_branches", DEFAULT_EXCLUDE))
    out = [default_branch]
    for b in paged(f"/repos/{repo}/branches", {"protected": "true"}):
        name = b["name"]
        if name == default_branch or exclude.search(name):
            continue
        active = get(f"/repos/{repo}/commits", {"sha": name, "per_page": 1,
                                                 "since": iso(ev["_start"]), "until": iso(ev["_end"])}).json()
        if active:
            out.append(name)
    return out


def iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- backlog
def backlog(repo, info, prs_all, issues_since, start, end_or_now):
    """Open issues and PRs at the event start and at its end (or now, while it runs).

    PRs come from the full PR list, so they are exact. For issues, those not touched since the
    start are still open (they would otherwise appear in issues_since), so
    open at T = untouched-open + issues seen since start that were created before T and not
    closed by T. An issue reopened after closing counts by its latest close only."""
    def ts(s):
        from datetime import datetime
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None

    def open_at(items, t):
        return sum(1 for i in items if ts(i["created_at"]) < t and (i["closed_at"] is None or ts(i["closed_at"]) >= t))

    open_prs_now = sum(1 for p in prs_all if p["state"] == "open")
    open_issues_now = info["open_issues_count"] - open_prs_now
    untouched = open_issues_now - sum(1 for i in issues_since if i["state"] == "open")
    return dict(
        repo=repo.split("/")[1],
        issues_start=untouched + open_at(issues_since, start),
        issues_end=untouched + open_at(issues_since, end_or_now),
        prs_start=open_at(prs_all, start),
        prs_end=open_at(prs_all, end_or_now),
    )


# ---------------------------------------------------------------- CI
def ci_status(get, repo, branches):
    """Latest non-PR run of each workflow on each tracked branch."""
    rows = []
    for br in branches:
        runs = get(f"/repos/{repo}/actions/runs", {"branch": br, "per_page": 100}).json().get("workflow_runs", [])
        seen = set()
        for r in runs:  # newest first
            # skip PR runs and GitHub's own dynamic jobs (Dependabot, Copilot), which aren't the repo's CI
            if r["event"] in ("pull_request", "pull_request_target") or (r.get("path") or "").startswith("dynamic/"):
                continue
            if r["path"] in seen:
                continue
            seen.add(r["path"])
            state = r["conclusion"] if r["status"] == "completed" else r["status"]
            rows.append(dict(key=f"{repo.split('/')[1]}:{br}:{r['name']}", repo=repo.split("/")[1], branch=br,
                             workflow=r["name"], state=state or "unknown", url=r["html_url"],
                             at=r["updated_at"]))
    return rows


# ---------------------------------------------------------------- releases
def unreleased(get, repo, default_branch, start, end):
    """Commits on the default branch since the latest release, and how many of them landed
    during the event."""
    rel = get(f"/repos/{repo}/releases/latest")
    if rel.status_code != 200:
        return dict(repo=repo.split("/")[1], release=None, release_date=None, commits_since=None, during_event=None)
    rel = rel.json()
    cmp = get(f"/repos/{repo}/compare/{rel['tag_name']}...{default_branch}").json()
    commits = cmp.get("commits", [])  # up to 250, newest last
    def ts(s):
        from datetime import datetime
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    during = sum(1 for c in commits if len(c.get("parents", [])) < 2
                 and start <= ts(c["commit"]["author"]["date"]) < end)
    return dict(repo=repo.split("/")[1], release=rel["tag_name"], release_date=(rel.get("published_at") or "")[:10],
                commits_since=cmp.get("ahead_by"), during_event=during)


# ---------------------------------------------------------------- tests and docstrings
TEST_DEF = re.compile(r"^\s*(?:async\s+)?def test\w*\s*\(", re.M)


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def count_tests(tree):
    n = 0
    for dirpath, dirnames, files in os.walk(tree):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in files:
            if not f.endswith(".py"):
                continue
            # every .py file: some repos (sasmodels) keep tests inside the package modules
            with open(os.path.join(dirpath, f), errors="ignore") as fh:
                n += len(TEST_DEF.findall(fh.read()))
    return n


def doc_coverage(tree, sources):
    dirs = [s for s in sources if os.path.isdir(os.path.join(tree, s))]
    if not dirs:
        return None
    # interrogate hits Python's recursion limit on some deeply nested files, so raise it
    runner = "import sys; sys.setrecursionlimit(50000); from interrogate.cli import main; main()"
    cmd = [sys.executable, "-c", runner, "--fail-under=0"]
    cfg = os.path.join(tree, "pyproject.toml")
    if os.path.exists(cfg) and "[tool.interrogate" in open(cfg, errors="ignore").read():
        cmd += ["-c", cfg]
    out = subprocess.run(cmd + dirs, cwd=tree, capture_output=True, text=True).stdout
    m = re.search(r"actual:\s*([\d.]+)%", out)
    return float(m.group(1)) if m else None


def code_metrics(repo, branches, sources, start, end_or_now, workdir):
    """Tests and docstring coverage on each tracked branch at the event start and now/end."""
    path = os.path.join(workdir, repo.replace("/", "__"))
    if not os.path.isdir(os.path.join(path, ".git")):
        os.makedirs(path, exist_ok=True)
        git("init", "-q", cwd=path)
        git("remote", "add", "origin", f"https://github.com/{repo}", cwd=path)
    since = (start - timedelta(days=45)).date().isoformat()  # enough history to find the start commit
    rows = []
    for br in branches:
        try:
            git("fetch", "-q", f"--shallow-since={since}", "origin", f"+{br}:refs/remotes/origin/{br}", cwd=path)
        except subprocess.CalledProcessError:
            git("fetch", "-q", "--depth=500", "origin", f"+{br}:refs/remotes/origin/{br}", cwd=path)
        ref = f"origin/{br}"
        sha_start = git("rev-list", "-1", f"--before={iso(start)}", ref, cwd=path)
        if not sha_start:  # branch quiet for a long time before the event: fetch further back
            subprocess.run(["git", "fetch", "-q", "--deepen=1000", "origin", f"+{br}:refs/remotes/origin/{br}"],
                           cwd=path, capture_output=True)
            sha_start = git("rev-list", "-1", f"--before={iso(start)}", ref, cwd=path)
        sha_end = git("rev-list", "-1", f"--before={iso(end_or_now)}", ref, cwd=path)
        vals = {}
        for label, sha in (("start", sha_start), ("end", sha_end)):
            if not sha:
                vals[label] = (None, None)
                continue
            tree = os.path.join(workdir, "_trees", f"{repo.replace('/', '__')}__{br}__{label}")
            if os.path.isdir(tree):
                subprocess.run(["git", "worktree", "remove", "--force", tree], cwd=path, capture_output=True)
            git("worktree", "add", "-q", "-f", "--detach", tree, sha, cwd=path)
            vals[label] = (count_tests(tree), doc_coverage(tree, sources))
            subprocess.run(["git", "worktree", "remove", "--force", tree], cwd=path, capture_output=True)
        rows.append(dict(key=f"{repo.split('/')[1]}:{br}", repo=repo.split("/")[1], branch=br,
                         tests_start=vals["start"][0], tests_end=vals["end"][0],
                         doc_start=vals["start"][1], doc_end=vals["end"][1],
                         sha_start=sha_start[:7] if sha_start else None, sha_end=sha_end[:7] if sha_end else None))
    return rows


def median_hours_to_merge(pr_rows, in_win):
    hrs = [(x["merged"] - x["created"]).total_seconds() / 3600 for x in pr_rows if x["new"] and in_win(x["merged"])]
    return round(statistics.median(hrs), 1) if hrs else None
