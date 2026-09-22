# NOAA OVATION snapshot archive

A byte-preserving archive of NOAA's numeric, global OVATION aurora forecast JSON for AuroraWatch. The collector uses Python 3.11+ and its standard library. It does not modify the AuroraWatch app.

**Deployment status (2026-09-22):** six-minute collection sessions and a native automatic handoff are deployed. [Run 35768554781](https://github.com/GefeiSHEN/NOAA-Aurora-Archive/actions/runs/35768554781) completed and its Actions token started [successor 35769270006](https://github.com/GefeiSHEN/NOAA-Aurora-Archive/actions/runs/35769270006), whose actor is `github-actions[bot]`. The successor collected and published a new snapshot at `18:45:26Z`. The original cron remains enabled as recovery; its sparse triggering alone did not provide the requested cadence. See `VERIFICATION.md` for exact timings and remaining outage limits.

Source: <https://services.swpc.noaa.gov/json/ovation_aurora_latest.json>

## Setup and manual runs

1. Publish this repository as **public**, with `main` as its default branch. Keep the implementation in a pull request until its merge is authorized.
2. Merge the reviewed implementation into the default branch. GitHub only schedules workflows present on that branch.
3. Ensure Actions is enabled under **Settings → Actions → General**, and repository/organization policy permits `actions/checkout` and the collector job's explicit `contents: write` permission. If branch protection requires PRs or prevents the Actions bot from pushing, collection will fail visibly; an owner must resolve that policy before deployment. No personal access token or paid service is needed.
4. Deploying changes to `scripts/**` or the collection workflow on `main` automatically starts a session through a `push` event. Data-only and documentation commits do not start sessions. A delayed session collects immediately, then on the UTC grid. The collection job deliberately skips private repositories and non-default branches.
5. **Actions → Collect NOAA OVATION → Run workflow** starts a manual six-minute debug session. Leave `predecessor_run_id` empty; turn off `continue_collection` for an isolated test. By default successful runs automatically start a successor. To stop collection, disable the workflow and cancel its active/pending runs. Merely disabling the schedule does not terminate an already running session.
6. Inspect actual data commits and collection times inside a running session. Each debug session remains **in progress** for about six minutes and can make successive data commits. Run names show the actual event, plus `handoff from <run ID>` when the preceding workflow requested it. GitHub categorizes these API-triggered successors as `workflow_dispatch`; their actor is `github-actions[bot]`, not the user. Confirm the source run's handoff log and the successor's start before claiming continuation is verified.

Local use:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/collect.py --root /tmp/ovation-preview
```

The local collector writes to the supplied directory and does not commit or push. Use one writer per directory. `python3 scripts/publish.py --branch "$DEFAULT_BRANCH"` downloads once and publishes through disposable worktrees in a Git checkout with an authenticated `origin`; it does not reset the caller's checkout. All collection workflow events currently execute `python3 -u scripts/session.py --branch "$DEFAULT_BRANCH" --minutes 6` to debug job completion and successor starts.

## Schedule and reliability

The native GitHub Actions cron is `2-59/5 * * * *`: UTC minutes 2, 7, 12, …, 57. This aims for two minutes after expected five-minute publication boundaries; neither NOAA publication nor GitHub execution is assumed punctual.

Sessions are temporarily set to **six minutes for handoff debugging**, starting once immediately and then waiting for the next absolute UTC slot. Slow downloads skip elapsed slots, with no synthetic backfill or burst of catch-up requests. An unchanged response succeeds and the session continues. Exhausted fetch/push retries fail the job visibly. The six-minute timer starts after setup; an in-flight publication may finish afterward. The whole job has an eight-minute hard safety timeout, overriding the publisher's ten-minute upper bound, so setup and normal shutdown have headroom without keeping a stalled debug job alive. Elapsed-time limits use a monotonic clock so wall-clock corrections cannot extend the session indefinitely.

This keeps one standard Linux runner allocated during the session, including between collections. It uses more runner time than one-shot jobs; public standard-runner execution is free, but consumes one concurrent runner slot. After collection succeeds, a separate short Linux job uses the built-in `GITHUB_TOKEN` to dispatch one successor on the default branch. Only this job receives `actions: write`; the collector retains only `contents: write`. The handoff job has no checkout or archive-write permission, retries its request at most three times, and has a two-minute timeout. Global workflow concurrency keeps the successor pending until the source workflow exits. The existing cron remains a recovery trigger.

This is an explicit native Actions continuation chain with six-minute collection windows, not an external scheduler or a personal access token. [GitHub permits workflow-dispatch events generated by GITHUB_TOKEN to start workflows](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow). A failed/canceled collection does not dispatch a successor; an exhausted handoff request fails visibly. Both cases await cron or an operator restart, so this still cannot guarantee uninterrupted coverage through GitHub or NOAA outages. Stopping collection requires disabling the workflow and canceling its active/pending runs, or running an isolated session with continuation disabled and no queued automatic replacement.

GitHub schedules can be delayed or dropped under load. Concurrency allows one running collector without canceling it; GitHub may replace an older pending run with a newer pending run. This is not a durable queue. In public repositories, schedules can be disabled after **60 days of repository inactivity**. Check Actions periodically and re-enable a disabled workflow when appropriate. See [GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) and [concurrency behavior](https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency).

The collector downloads an HTTP 200 response with a 30-second socket timeout and a 5 MiB body limit. It validates before writing. Network failures, HTTP 408/429/5xx, and invalid/incomplete responses receive up to three retries after 15, 30, and 60 seconds. `Retry-After` seconds or HTTP dates can extend a delay up to 120 seconds. Other HTTP errors fail immediately. Exhaustion returns a nonzero exit status and fails the Actions run. Git commands each have a 60-second timeout.

Validation requires a JSON MultiPoint, the expected data format, timezone-qualified observation/forecast timestamps, forecast time at or after observation, and all 65,160 unique one-degree grid coordinates: longitude 0…359 and latitude −90…90. Triples must be finite numeric values, aurora values 0…100, with both hemispheres represented. A future upstream schema/grid change intentionally fails closed and requires review. Delayed/stale but structurally valid data is retained with its real times; there is no invented freshness threshold.

Invalid responses leave all existing files untouched, including the latest index. Successful writes use atomic file replacement and are published together in one Git commit. A local filesystem failure could interrupt a multi-file update; the workflow never pushes that partial worktree. Each failed push is retried from a newly fetched remote head, recomputing revisions and indexes against that state while retaining the original downloaded bytes and collection time. There are at most three publication attempts. No force-push or overwrite of existing snapshot bytes is used.

## Files and schemas

Snapshot paths are relative to the repository root:

```text
YYYY/MM/DD/YYYYMMDDTHHMMSSZ.json
YYYY/MM/DD/YYYYMMDDTHHMMSSZ-<full-sha256>.json  # revision
metadata/YYYY/MM/DD.json                     # observation-day metadata index
latest.json
recent.json
```

The path comes from NOAA's **Observation Time**, normalized to UTC. Snapshot content is the exact downloaded body, including its original JSON formatting, coordinates, and values. `.gitattributes` disables Git newline conversion for snapshot files. No hemisphere mirroring, interpolation, or invented frames is performed.

A SHA-256 hash over the downloaded bytes identifies a snapshot. An identical response is a successful no-op, including a return to a previously archived revision. Byte changes at the same observation timestamp produce a new full-hash-suffixed file. Even formatting-only upstream changes count as revisions. The first copy keeps the unsuffixed path; existing snapshots are never replaced.

Every index uses `schema_version: 1`. Each snapshot entry has this shape (illustrative values):

```json
{
  "path": "2026/09/22/20260922T084500Z.json",
  "observation_time": "2026-09-22T08:45:00Z",
  "forecast_time": "2026-09-22T10:11:00Z",
  "collected_at": "2026-09-22T08:57:31Z",
  "source_url": "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json",
  "sha256": "4ea0cd059f4c9c2cdc89ac3860fbab49a08a0f401d8968658f5cf792e7411151",
  "http_validators": {
    "etag": "\"e02a9-65c0e83722de3\"",
    "last-modified": "Tue, 22 Sep 2026 08:54:24 GMT"
  }
}
```

- **Observation time** is NOAA's observation/input time label; it names the frame and archive path.
- **Forecast time** is NOAA's forecast target time, not the time the archive downloaded it.
- **Collection time** is when the successful HTTP body finished downloading. Repeated identical responses do not refresh this first-seen time. HTTP validators are captured from that first successful response when available; absent validators are omitted, leaving `{}` if neither is supplied. They do not determine uniqueness.

The durable `metadata/YYYY/MM/DD.json` has `{ "schema_version": 1, "snapshots": [...] }`, sharded by **observation date**, and keeps all entries. Its entries are sorted descending by observation time, collection time, then hash.

`latest.json` has `{ "schema_version": 1, "snapshot": { ... } }`. It references the greatest observation time, then the most recently collected revision at that observation time; hash breaks exact timestamp ties deterministically. An older observation arriving later cannot move this index backward. This is a pointer, not a copy of NOAA's payload.

`recent.json` has `{ "schema_version": 1, "window_hours": 24, "snapshots": [...] }`. It includes distinct snapshots first collected within the preceding 24 hours at the last successful collector run, including revisions, sorted descending by collection time, observation time, then hash. The exact 24-hour boundary is inclusive. It can be empty. An unchanged response may prune expired entries and commit that index change, but creates no new snapshot or metadata record. No run-time timestamp is rewritten merely to produce a heartbeat commit.

If runs stop or fail, these indexes freeze. Consumers must check timestamps themselves and filter `recent.json` against their current clock. Indexes are created on the first valid collection; there are no placeholder forecast frames.

## Consumer URLs

Public consumer URLs for `GefeiSHEN/NOAA-Aurora-Archive` on `main`:

- Latest pointer: <https://raw.githubusercontent.com/GefeiSHEN/NOAA-Aurora-Archive/main/latest.json>
- Recent 24-hour index: <https://raw.githubusercontent.com/GefeiSHEN/NOAA-Aurora-Archive/main/recent.json>
- Snapshot: prefix `https://raw.githubusercontent.com/GefeiSHEN/NOAA-Aurora-Archive/main/` to an entry's `path`.
- Historical metadata example: <https://raw.githubusercontent.com/GefeiSHEN/NOAA-Aurora-Archive/main/metadata/2026/09/22.json>

These default-branch URLs serve the published archive; check the entry timestamps for freshness. If you rename the owner, repository, or default branch, update them. GitHub raw URLs may be cached. Fetch an index, use its immutable snapshot path, and verify the body hash. For a consistent multi-index view, use one Git commit SHA in place of `main` for every request.

## Coverage and storage limits

This archive starts collecting **from deployment onward**; the included local verification frame is explicitly a pre-deployment sample. NOAA's latest-only endpoint cannot recover missed numeric frames. No backfill, hemisphere reconstruction, or continuous coverage is claimed.

Only standard Linux GitHub-hosted runners (`ubuntu-latest`) are used. Their execution is [free for public repositories](https://docs.github.com/en/billing/concepts/product-billing/github-actions). The workflow uses no paid services, external scheduler, Actions artifacts, Actions caches, or Git LFS. Free runner usage does **not** mean unlimited repository or artifact storage.

The archive retains all unique snapshots. Files, metadata, commits, and Git history grow continuously. At roughly 0.9 MB per response and 288 unique frames/day, raw working-tree growth could approach 260 MB/day before Git compression; actual size depends on publication frequency and compression. Monitor repository size and GitHub's current [repository limits](https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits). Deleting files in later commits does not remove their historical Git storage. There is deliberately no automatic deletion or history rewrite; choose a reviewed retention/rotation policy as the archive grows.
