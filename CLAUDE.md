# CLAUDE.md — SasView event dashboard

Context for Claude Code sessions working in this repo. README.md is the user-facing manual;
this file holds the decisions, history and gotchas behind it.

## What this is

A public GitHub Pages dashboard of GitHub activity during time-boxed SasView events
(Contributor Camps first; also virtual hackathons and sprints). Live at
https://www.sasview.org/event-dashboard/ (the org site's custom domain covers project pages;
`sasview.github.io/event-dashboard` redirects there). Projector view: `?tv=1`.

Owner: Paul Butler. Built with Claude on 8 Nov 2026 during Contributor Camp XV (Prague).
Maintainers: Paul, Claude, any SasView developer.

## Layout

- `events/*.toml` — one file per event (title, dates, time zone, repos, per-repo `source`/`branches`).
- `collector/collect.py` — activity inside the window (PRs, issues, commits, reviews, first-timers, daily series).
- `collector/extras.py` — before/after and status: backlog, tracked branches, CI, tests + docstring
  coverage (interrogate), unreleased commits, median time to merge.
- `site/` — static page (index.html, app.js, style.css, vendored d3 in `site/vendor/`). No build step.
- `archive/<id>/` — frozen final JSON for finished events (written by the Archive workflow).
- `.github/workflows/dashboard.yml` — hourly (gated: only within ±1 day of an event), on push, and manual.
- `.github/workflows/archive.yml` — manual; freezes an event and triggers a rebuild.

Run locally: `pip install -r requirements.txt; python collector/collect.py; python -m http.server -d site 8000`.

## Decisions (and why)

- **Everyone counts**: all activity in the window counts, not only attendees, because many people
  contribute remotely. Bots (Dependabot, pre-commit) are excluded.
- **Closed-PR categories** (Paul's "new work vs cleanup" question): new & merged; older reworked
  (commits during event) & merged; backlog merged as-is; older closed unmerged (cleanup); new closed unmerged.
- **Issue closed "by a PR"**: closing event has a commit_id, or a merged cross-referenced PR within
  10 minutes of the close. Most camp closes were by hand (triage, already-fixed).
- **Tracked branches** = default branch + protected branches not matching `^release` that had commits
  in the window (Paul's insight: protection marks shared dev/release branches). For Camp XV that is
  main/master + `refactor_24` (data/plotting refactor) in sasview and sasdata.
- **Tests** = `def test…` in any .py file (sasmodels keeps tests inside the package; its per-model
  `tests = [...]` tables are not counted). Docstrings = interrogate on `source` folders, run with a
  raised recursion limit (sasmodels crashes it otherwise).
- **Data not committed**: built into the Pages artifact each run, so no hourly data commits.
- **No per-person leaderboard**: deliberate, to avoid discouraging newcomers; first contributions are celebrated instead.
- **d3 is vendored**, not loaded from a CDN.
- **Cache busting**: index.html references `app.js?v=__BUILD__`; the workflow stamps the commit SHA.
  Open pages poll index.html every 10 min and reload fully if the build changed (keeps the projector current).

## Lessons learned / gotchas

- Browsers cached the old app.js after a deploy, giving the new HTML with old JS (empty tables,
  shifted columns). Fixed by the cache busting above; tell users to hard-refresh after a change if in doubt.
- The first GitHub-scheduled run in a new repo can be delayed a long time; schedules are best-effort.
  Scheduled workflows are disabled after 60 days without repo activity: re-enable under Actions before an event.
- Big jumps in test/docstring numbers can come from long-lived branches merged during an event
  (Camp XV: sasdata `fair_database` into `refactor_24`: +245 tests, docstring 50%→38%). The page says
  changes include work merged, not only written, during the event.
- Claude cloud sessions: GitHub GraphQL is blocked and REST pagination `next` links (which use
  `/repositories/<id>`) are refused by the proxy; the collector paginates with `?page=N` instead.
  sasview.org / github.io can't be fetched from the sandbox, so live-page checks need the user (or the
  deployment status API: `environment_url` on the latest github-pages deployment).
- Validate counts against GitHub search (`repo:SasView/x is:pr merged:>=…`) when changing logic; the
  only expected difference is bot PRs.

## History

- First version was a Claude Dashboard artifact (claude.ai), refreshed hourly by a Claude scheduled
  task ("SasView camp dashboard refresh"). Only visible to logged-in Claude users, so it was moved here.
- Piotr Rozyczko (camp attendee) built https://github.com/easyscience/org-health the same day: a
  multi-repo health board (radon complexity, interrogate, Codecov, CI duration, PR size, release drift,
  history.json appended from the live site). Backlog/CI/PR-size/release panels here follow his ideas.

## Open items (as of 8 Nov 2026, evening)

- Confirm the hourly scheduled run fires (none had by 21:30; only push builds).
- Once confirmed: delete the Claude scheduled task "SasView camp dashboard refresh" (Paul asked to keep it until then).
- After Camp XV ends (Tue 10 Nov): run Actions → Archive an event → `contributor-camp-xv`.
- Possibly rename the repo to `dashboards` and add a /health page (adapting org-health). If renamed,
  fix the page link on the ContributorCampXV wiki page. org-health has no licence: ask Piotr to add
  BSD-3 or contribute his collector here himself.
- Health-page candidates for events later: commits not yet released (kept), PR size, CI duration.
