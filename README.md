# SasView event dashboard

A public, live progress page for SasView Contributor Camps, virtual hackathons, sprints and
any other time-boxed event. It shows what happened on GitHub during the event: PRs opened and
merged, issues opened and closed, commits, reviews, first-time contributors, and the split
between new work and cleanup of older PRs and issues.

**Live page:** https://sasview.github.io/event-dashboard/

- Pick an event: `?event=<id>` (or the menu at the top right when there is more than one)
- Projector view, with large text for a screen in the room: add `?tv=1`, e.g.
  https://sasview.github.io/event-dashboard/?tv=1

The page reloads its data every 10 minutes. The data itself is refreshed hourly while an event
is running.

## How it works

```
events/*.toml          one small file per event (title, dates, time zone, repos)
collector/collect.py   pulls activity from the GitHub REST API and writes JSON
site/                  the static page (index.html, app.js, style.css) + generated site/data/
archive/<id>/          frozen final numbers for finished events
.github/workflows/
  dashboard.yml        hourly: collect + publish to GitHub Pages (skips when no event is running)
  archive.yml          run by hand after an event to freeze its numbers
```

The hourly workflow checks whether any event is running (from one day before its start to one
day after its end). If none is, it stops straight away, so it costs nothing between events. The
generated data is published with the page and is **not** committed to the repo, so the history
isn't cluttered with hourly data commits.

## Adding a new event

1. Copy an existing file in `events/` to a new name, e.g. `events/2027-03-virtual-hackathon.toml`.
2. Edit it:

   | Key | Required | Meaning |
   |---|---|---|
   | `id` | yes | Short name used in the URL (`?event=...`). Letters, digits, `-`, `_`. |
   | `title` | yes | Heading shown on the page, e.g. `"SasView Contributor Camp XVI"`. |
   | `subtitle` | no | Line under the title, e.g. a venue or "virtual". |
   | `link` | no | Link to the event's wiki or info page. |
   | `start`, `end` | yes | First and last day, inclusive, as `YYYY-MM-DD`. |
   | `timezone` | yes | Days are counted in this zone, e.g. `"Europe/Prague"`, `"America/New_York"`, `"UTC"` for virtual events. |
   | `repos` | yes | List of repos, e.g. `["SasView/sasview", "SasView/sasmodels", "SasView/sasdata"]`. |
   | `baseline_weeks` | no | Weeks before the event used for "typical week" figures (default 8). |

3. Commit to `main`. The push triggers a build, and from the day before the event starts the page
   updates hourly by itself. Until the event starts, it isn't listed on the page.

To refresh immediately, open the **Actions** tab → **Build and publish dashboard** →
**Run workflow**.

## After an event

Open **Actions** → **Archive an event** → **Run workflow**, and enter the event's `id`. This
saves the final numbers in `archive/<id>/` and rebuilds the page. From then on the event is shown
from the archive and is never fetched again, which keeps hourly runs fast as events accumulate.

## What is counted

- **Everything in the window counts**, whether or not the person was at the event, since many
  people contribute remotely. Bot accounts (Dependabot, pre-commit) are left out.
- **Window**: 00:00 on `start` to 24:00 on `end`, in the event's time zone.
- **Closed PRs** are split into: new work merged; older PRs reworked (new commits during the event)
  and merged; backlog PRs merged as-is; older PRs closed without merging (cleanup); new PRs
  closed without merging.
- **Commits**: non-merge commits authored during the event on the default branch or on any PR
  branch active during the event, each counted once.
- **Issues closed by a PR**: the closing event names a commit, or a merged PR referencing the
  issue landed within ten minutes of the close. Everything else counts as closed by hand.
- **First-time contributors**: people whose first-ever PR to the listed repos was opened during
  the event.
- **AI-assisted PRs**: PRs with a `Co-authored-by:` trailer naming Claude, Copilot, Cursor, Codex
  or Gemini in a commit or the description.
- **Not counted**: discussions, wiki edits and PR comments that are not reviews.

The full definitions are also at the bottom of the page.

## Running locally

```bash
pip install requests
export DASHBOARD_TOKEN=<a GitHub token>   # optional, raises the API rate limit
python collector/collect.py               # writes site/data/
python -m http.server -d site 8000        # then open http://localhost:8000
```

Python 3.11 or newer is needed (for `tomllib`).

## Changing the page

The page is plain HTML, CSS and JavaScript with [d3](https://d3js.org) loaded from a CDN. There
is no build step: edit `site/index.html`, `site/app.js` or `site/style.css`, run the collector
once locally to get data, and preview with `python -m http.server`. New figures are added in
`collector/collect.py` (each JSON file in `site/data/<id>/` is one dataset) and drawn in
`site/app.js`.

## Repository settings this needs

- **Settings → Pages → Source: GitHub Actions.**
- Actions enabled for the repo. The workflows use the built-in `GITHUB_TOKEN` only, and no
  secrets need adding.

Note that GitHub turns off scheduled workflows in a repo that has had no activity for 60 days,
which is likely between events. If the hourly refresh doesn't start for a new event, open
**Actions** → **Build and publish dashboard** and click **Enable workflow**.
