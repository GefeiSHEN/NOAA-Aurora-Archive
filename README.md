# NOAA Aurora Archive

NOAA's numeric OVATION aurora forecasts, preserved byte-for-byte for both hemispheres and published for AuroraWatch. Python 3.11+ and native GitHub Actions; no third-party Python dependencies.

Source: [NOAA OVATION JSON](https://services.swpc.noaa.gov/json/ovation_aurora_latest.json).

## Layout

```text
OVATION/
  YYYY/
    MM/
      DD/
        YYYYMMDDTHHMMSSZ.json
        YYYYMMDDTHHMMSSZ-<sha256>.json     # changed-content revision
        metadata.json                    # metadata for this day
  latest.json
  recent.json
scripts/                                 # collection and workflow runtime
tests/
agents/                                  # internal history and maintenance
```

Canonical folders and filenames use NOAA's **Forecast Time in UTC**: the predicted time, not Observation Time or the time this archive downloads it. The original JSON body, coordinates, values, and formatting remain unchanged. Git newline conversion is disabled for snapshots.

Forecast time identifies a frame. Identical bytes for an already archived forecast are a successful no-op, even if fetched again later. Changed bytes for that forecast become a full-SHA-256-suffixed revision; they never overwrite the first copy. Observation time remains in metadata. There is no hemisphere mirroring, interpolation, or invention of missing frames.

## Consumer URLs and indexes

- [Latest index](https://raw.githubusercontent.com/GefeiSHEN/NOAA-Aurora-Archive/main/OVATION/latest.json)
- [Recent 24-hour index](https://raw.githubusercontent.com/GefeiSHEN/NOAA-Aurora-Archive/main/OVATION/recent.json)
- Daily metadata: `https://raw.githubusercontent.com/GefeiSHEN/NOAA-Aurora-Archive/main/OVATION/YYYY/MM/DD/metadata.json`

Every entry's `path` is **repository-root-relative**, including `OVATION/`. Append it to `https://raw.githubusercontent.com/GefeiSHEN/NOAA-Aurora-Archive/main/` and verify the downloaded SHA-256. Raw URLs may be cached; use a single Git commit SHA instead of `main` for a consistent multi-index view.

The previous root-level indexes and observation-based filenames have migrated. Consumers using old URLs must update them. Historical commit URLs retain their old layout.

All indexes have `schema_version: 1` and use these snapshot fields:

| Field | Meaning |
| --- | --- |
| `path` | Canonical JSON path, e.g. `OVATION/2026/09/22/20260922T200500Z.json` |
| `forecast_time` | NOAA's forecast target; determines folders, filename, and frame identity |
| `observation_time` | NOAA's observation/input timestamp |
| `collected_at` | When the successful HTTP body finished downloading |
| `source_url` | Original NOAA endpoint |
| `sha256` | SHA-256 of the exact downloaded bytes |
| `http_validators` | Available `etag` and `last-modified` headers; absent headers are omitted |

`latest.json` wraps one entry as `{"schema_version": 1, "snapshot": {...}}`. It selects the newest forecast time, then newest collected revision; hash breaks exact ties. An older forecast cannot move it backward, even if its observation time is newer.

`recent.json` uses `{"schema_version": 1, "window_hours": 24, "snapshots": [...]}`. It covers distinct snapshots first collected in the preceding 24 hours at the last successful run, including revisions. Entries are ordered by collection time, forecast time, then hash, descending; the 24-hour boundary is inclusive. Identical responses do not refresh collection times but may prune expired entries.

Daily metadata uses `{"schema_version": 1, "snapshots": [...]}`, retaining all entries for that **forecast date**, ordered by forecast time, collection time, then hash, descending. When collection stops or fails, indexes freeze. Consumers must check timestamps against their own current clock; a future forecast time does not prove a recent download.

## Automatic collection

Each workflow plans a **23-hour 59-minute 30-second collection window** (86,370 seconds) and hands off to the next workflow after success. Since a standard GitHub-hosted job is limited to six hours, five sequential Linux jobs share one absolute deadline. Each segment normally receives 17,274 seconds (4h 47m 54s), with a 300-minute hard job limit. Queue/setup time consumes the shared window; later jobs shorten accordingly. Preparation, cleanup, and handoff can make total workflow elapsed time longer than the collection window. See [GitHub's execution limits](https://docs.github.com/en/actions/reference/limits).

Each job collects immediately on startup, then every **two minutes on even UTC minutes**. Slow downloads skip elapsed slots without inventing catch-up frames. Successful unchanged responses continue. A monotonic timer bounds each job; the shared deadline bounds the whole collection window. A publisher interrupted at that deadline is terminated with its Git subprocesses, preventing a stale process from continuing after handoff.

The original cron `2-59/5 * * * *` remains a recovery trigger; GitHub cron cannot run more often than every five minutes. Normal two-minute polling happens inside the running jobs. Global workflow concurrency allows only one active cycle and does not cancel it; newer pending runs may replace older pending runs. A push that deploys collector code or its workflows starts a cycle; data/documentation commits do not.

The collection jobs receive only `contents: write`. A separate handoff job receives only `actions: write` and uses the built-in `GITHUB_TOKEN` to dispatch a successor on the default branch. Its requests have three bounded attempts and a two-minute job limit. Successor events are `workflow_dispatch`, their actor is `github-actions[bot]`, and their titles identify the predecessor. A failed or canceled collection does not launch a successor; exhausted handoff requests fail visibly and await cron or operator recovery. No personal token or external scheduler is used.

GitHub schedules can be delayed or dropped, and public scheduled workflows may be disabled after 60 days of repository inactivity. Handoffs and runners can also fail. Missed NOAA numeric frames cannot be recovered from the latest-only endpoint. This archive starts collecting from deployment onward, with an explicitly documented pre-deployment verification sample; uninterrupted historical coverage is not claimed. See [GitHub scheduling behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Validation and publication

Responses must contain the expected MultiPoint structure, timezone-qualified timestamps, forecast time at or after observation, and all 65,160 unique grid triples spanning longitude 0…359 and latitude −90…90. Values must be finite numbers and aurora values 0…100. Invalid/incomplete responses leave all existing files unchanged.

Network errors, HTTP 408/429/5xx, and invalid responses receive up to three retries after 15, 30, and 60 seconds. `Retry-After` can extend delays up to 120 seconds. HTTP reads have a 30-second socket timeout and a 5 MiB limit. Other HTTP errors fail immediately. Each fetch/publication process has a ten-minute upper bound, shortened by the cycle deadline.

Publication uses a disposable Git worktree and one atomic Git commit. Push conflicts retry against the newest remote head while retaining downloaded bytes and their original collection time. There is no force-push or overwrite of existing snapshot bytes. Unchanged archive/index content creates no commit. Git commands have 60-second timeouts. Upstream schema changes intentionally fail closed for review.

## Setup and operation

1. Keep the repository **public**, with workflows on the default branch (`main`). Enable Actions and permit `actions/checkout` plus the job-scoped token permissions. Branch protection must allow data pushes.
2. Use **Actions → Collect NOAA OVATION → Run workflow** to start collection manually. Leave `predecessor_run_id` empty and keep `cycle_seconds` at `86370` for normal operation. Successful cycles continue automatically.
3. For a short end-to-end test, reduce `cycle_seconds` (minimum 30). It is split across the same five jobs. Disable `continue_collection` for an isolated test; otherwise its successor uses the normal 86,370-second default, not the shortened duration.
4. To stop collection, disable the workflow and cancel its active/pending runs. Disabling the schedule alone does not stop an active workflow.

```sh
python3 -m unittest discover -s tests -v
python3 scripts/collect.py --root /tmp/aurora-preview
```

The local collector writes `/tmp/aurora-preview/OVATION/` and never commits or pushes. Use one writer per directory. `scripts/publish.py --branch main` publishes once using authenticated `origin`; `scripts/session.py --branch main --seconds 120` runs a bounded local session. Internal verification history and the one-time layout migration helper live in `agents/`.

## Cost and retention

Standard `ubuntu-latest` execution is [free for public repositories](https://docs.github.com/en/billing/concepts/product-billing/github-actions), including waiting between collections. Workflows skip private repositories. There are no paid runners, external schedulers, Actions artifacts/caches, or Git LFS. A cycle uses one active runner slot at a time; free execution does not mean unlimited storage.

All unique snapshots are retained. At roughly 0.9 MB per response and 720 polls/day, uncompressed growth could approach 660 MB/day if every response differs; duplicate responses add no files. Monitor repository size and [GitHub repository limits](https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits). Files, metadata, and Git history grow; deleting files does not remove their historical storage. No automatic deletion or history rewriting is configured.
