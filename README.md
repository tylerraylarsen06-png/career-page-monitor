# career-page-monitor

I built this to stop refreshing career pages. It watches the student and internship pages of eleven consulting and finance firms (McKinsey, Bain, BCG, Kearney, Oliver Wyman, Roland Berger, Goldman Sachs, Morgan Stanley, J.P. Morgan, Citi, Bank of America) and sends one Discord message when something on them actually changes. An AI pass reads each change and says whether it matters, so most days there is nothing to read and the phone stays quiet.

It has no server, no database and no hosting bill. Everything runs inside GitHub.

## The design

```
GitHub Actions cron (daily, 06:00 UTC)
        │
        ▼
  run.py ── for each config/*.yaml ──► fetch page ─► scope to the content ─► normalize ─► hash
        │                                                          │
        │                                   compare to state/<watch>.json (last run's hash + text)
        │                                                          │
        ▼                                                          ▼
  translate.py  ◄── word-level diffs of every changed page ──  changed watches
  (Gemini judges each diff against profile.md and the watch's goal)
        │
        ▼
  digest.py ─► one Discord message (ping only if something is worth acting on)
        │
        ▼
  workflow commits state/ back to this repo  ◄── the repo is the database
```

### The repo is the database

Each watch keeps its last-seen state in `state/<watch>.json`: a content hash, the full normalized page text (capped at 20,000 characters), and a failure counter. After every run the workflow commits `state/` back to the repo. The next run checks out the repo and reads its own previous output.

That one decision removes the server and the database. It also gives a free audit log, because the git history of `state/` is the history of every page.

### Zero servers, zero cost

A daily run takes about one to two minutes on a Linux runner. That is roughly 30 to 60 minutes a month, which a public repo gets free and which uses about 2 to 3 percent of the 2,000 free minutes a private repo gets. The Gemini call is one batched request per run, inside the free tier.

### Detecting change without false alarms

Most of the work is in not reporting things that did not happen.

- **Scope before hashing.** Each watch names a CSS selector, regex or JSON path, so a rotating banner or a new blog post elsewhere on the page never counts as a change.
- **Soft-404 protection.** A watch is marked failed, never changed, if the request errors, the selector matches nothing, or the text comes back shorter than `min_content_length`. A Cloudflare challenge page or a redirect to an error page is short, so it trips this check and never overwrites good state.
- **Failures wait their turn.** One failed fetch is logged and ignored. A watch only shows up under FAILURES after three consecutive failed runs, so network blips do not create noise.

### The AI layer is a filter, not the detector

Change detection is a plain hash comparison and does not depend on the model. `translate.py` runs afterwards. It builds a word-level diff for every changed page (the pages are normalized to one line, so a line diff would be useless), then sends all the diffs in one prompt to Gemini along with `profile.md`, which says who the alerts are for and what counts, and each watch's `goal`.

Gemini answers with one line per change worth acting on, or `NOTHING_MEANINGFUL`. It fails soft in every direction: no key, a failed call or a malformed reply all fall back to the plain before/after format. A bad API call can never be the reason a real change goes unreported.

### Ping or post silently

Every run with something to report posts to Discord, so the channel is a full log. Whether it also @-mentions me depends on the verdict:

| Situation | Posts | Pings |
|---|---|---|
| Gemini judged a change meaningful | Yes | Yes |
| Gemini judged every change as noise | Yes | No |
| Gemini unavailable but something changed | Yes | Yes, because an unknown is not safe to ignore |
| Only failures | Yes | No |

With the channel set to "Only @mentions," the phone buzzes only when there is something to do.

### Keeping a free cron alive

GitHub disables scheduled workflows after 60 days without repository activity. A healthy page can easily go 60 days without changing, which would quietly kill the monitor. `run.py` rewrites `state/_heartbeat.json` with a timestamp on every run, so every run produces a commit and the 60-day timer never runs out.

### A known limit

Kearney's careers page sits behind Cloudflare bot management and returns a 403 challenge to plain HTTP requests. Fixing that needs a headless browser, which is heavier than this design calls for, so that watch is expected to sit in FAILURES. The failure handling above is what keeps it from ever being reported as a change.

## Files

| Path | What it does |
|---|---|
| `config/*.yaml` | One file per watch: URL, type, selector, minimum length, goal |
| `monitors/` | One fetcher per source type: `html_page_diff.py`, `rss_atom.py`, `json_api.py`, plus shared normalizing, hashing and diffing in `common.py` |
| `run.py` | Loads every watch, diffs it against state, writes the heartbeat, calls the AI pass and the digest |
| `translate.py` | Optional Gemini judgment of each diff |
| `digest.py` | Builds one Discord message under the 2,000 character limit, trimming failures before changes |
| `profile.md` | Plain-text description of who the alerts are for (an example is included) |
| `.github/workflows/monitor.yml` | The cron, the run, and the commit of `state/` |

## Adding a watch

Drop a YAML file in `config/`. Its first run sets a baseline without posting, and changes are reported from then on.

```yaml
name: "Human-readable name"
url: "https://example.com/careers"
type: html                 # html | rss | json
selector: "main"           # html only
regex: 'Applications (are now \w+)'   # optional
json_path: "data.jobs"     # json only
min_content_length: 500
notify: discord
goal: >
  Alert when applications open or a deadline changes.
```

## Running it yourself

Set these repository secrets (Settings → Secrets and variables → Actions):

| Secret | Purpose |
|---|---|
| `DISCORD_WEBHOOK_URL` | Required. Where the digest is posted |
| `GEMINI_API_KEY` | Optional. Turns on the AI judgment pass |
| `DISCORD_USER_ID` | Optional. Turns on @-mention pings |

Then run it by hand once:

```
gh workflow run monitor.yml
```

Locally:

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 run.py
```

This repository is a public copy of the one I run privately. Scheduled runs are off here because the secrets are not set.
