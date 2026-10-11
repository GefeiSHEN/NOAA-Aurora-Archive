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
  archive.json                           # days moved to release assets
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

Every entry's `path` is **repository-root-relative**, including `OVATION/`. Append it to `https://raw.githubusercontent.com/GefeiSHEN/NOAA-Aurora-Archive/main/` and verify the downloaded SHA-256. Raw URLs may be cached; use a single Git commit SHA instead of `main` for a consistent multi-index view, and only for a short time.

When the archive moves to its own `data` branch (see [Data branch and monthly history reset](#data-branch-and-monthly-history-reset)), replace `main` with `data` in every URL above. Paths and file contents stay the same.

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

`main` keeps the current UTC day and the four before it, so the last 72 hours are always there. Older day folders move to release assets (see [Retention](#cost-and-retention)); their `path`s no longer resolve on `main`.

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

All unique snapshots are retained; none are deleted. Git delta-compresses the near-identical snapshots, so the stored repository grows about 10 MB/day even though a day's files total about 250 MB. Monitor repository size and [GitHub repository limits](https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits). History rewriting is opt-in; see [Data branch and monthly history reset](#data-branch-and-monthly-history-reset).

The collector checks out only `latest.json`, `recent.json` and the snapshot's day folder, so its disk use and time per poll do not grow with the archive.

**Retention on `main`.** The daily **Archive old OVATION days** workflow (`scripts/archive_days.py`) keeps five day folders on `main`. Each older folder is packed as `OVATION-YYYY-MM-DD.tar.xz` (about 1–3 MB) on the release `ovation-YYYY-MM`. The folder is removed from `main` only after the asset is uploaded, downloaded back, and every file in it matches the Git blob it came from. `OVATION/archive.json` lists each archived day newest first:

| Field | Meaning |
| --- | --- |
| `date`, `path` | Forecast date and the folder it came from |
| `release`, `asset`, `url` | Where to download the tarball |
| `sha256`, `bytes`, `files` | Digest and size of the tarball, number of files inside |
| `tree` | Git tree of the folder; unchanged folders only are removed |
| `commit` | A commit that held the folder. On `main` it keeps working; on the `data` branch it stops resolving after the next monthly reset, and the tarball (or the history bundle) is the copy to use |

Extract with `tar -xJf OVATION-YYYY-MM-DD.tar.xz`; it restores `OVATION/YYYY/MM/DD/` byte for byte, including `metadata.json`. Run the workflow with **dry_run** to build and verify without uploading or changing `main`.

## Data branch and monthly history reset

Every snapshot is a commit, so history grows by about 10 MB a day forever, even though `main` only keeps five days of files. To cap that, the archive can live on its own branch whose history restarts once a month. Code and workflows stay on `main`, whose history is never rewritten.

It is off until the repository variable **`OVATION_DATA_BRANCH`** is set (Settings → Secrets and variables → Actions → Variables), normally to `data`. Before that, everything runs on `main` exactly as described above.

**Switching over.** Set the variable. The next collection segment (within about five hours) finds no `data` branch and starts it as one parentless commit holding `main`'s `OVATION/` folder, then publishes there from then on. The archiver follows the same variable. Consumers must change `/main/` to `/data/` in their raw URLs at this point. `main`'s copy stops updating, and the first reset removes it with an ordinary commit once it confirms the `data` branch started from exactly that copy.

**Monthly reset.** The **Reset OVATION data history** workflow (`scripts/reset_history.py`) runs on the 1st of each month at 04:23 UTC and only when the variable is set. It:

1. Refuses unless every day folder that the branch's history ever removed is listed in `OVATION/archive.json` and its asset is on its release with the recorded size.
2. Saves the branch's whole history as `ovation-data-YYYY-MM-DD-<commit>.bundle` on the release `ovation-history`, downloads it back, compares SHA-256, and fetches it into an empty repository with object checks to prove it restores the old head.
3. Replaces the branch with one parentless commit whose files are exactly the old head's, using `--force-with-lease`. If the collector pushed meanwhile, it bundles just the new commits as a `-part.bundle` and tries again from the new head, so no commit is dropped unsaved.

Run it from **Actions → Reset OVATION data history → Run workflow**; manual runs default to **dry_run**, which does all checks and builds and verifies the bundle without uploading or pushing. A dry run works before switching over too; it then checks `main` and never rewrites it. Restore a month with `git fetch <bundle> 'refs/ovation/history:refs/ovation/history'`, oldest bundle first.

**What changes for you.**

- Raw URLs on `/data/` keep working through a reset because the files are identical. URLs pinned to a `data` commit SHA stop working once GitHub garbage-collects the old commits.
- Existing clones of `data` see a forced update; run `git fetch origin && git reset --hard origin/data`. Never merge an old `data` checkout back, or the old history returns until the next reset.
- GitHub keeps unreachable objects until its own garbage collection, so the reported repository size drops later, not at the reset. History already on `main` (and pinned by pull request refs) stays; what stops is the growth.
- GitHub refuses pushes from the built-in Actions token that add or change workflow files. `data` holds none, so the built-in token can reset it; rewriting `main` would need a personal token with workflow scope, which this project avoids. Branch protection, if added to `data`, must allow force pushes from GitHub Actions.
