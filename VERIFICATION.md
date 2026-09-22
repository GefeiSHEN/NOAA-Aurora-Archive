# Initial verification — 2026-09-22

- `python3 -m unittest discover -s tests -v`: **15 tests passed** on Python 3.12 locally. Fixtures are generated in tests and never enter the real archive.
- Integration test uses a local bare Git remote and a competing writer to force a rejected push. Retrying preserves both snapshots, retains the newer latest pointer, and leaves the original checkout and its uncommitted file untouched. Publishing the same bytes again makes no commit.
- Both workflow YAML files parse successfully. The initial implementation also passed all 15 tests on a standard GitHub-hosted Linux runner: [PR CI run](https://github.com/GefeiSHEN/NOAA-Aurora-Archive/actions/runs/35708573451). The PR checks track validation of subsequent commits.
- Real collection: `python3 scripts/collect.py` downloaded directly from NOAA and wrote the included pre-deployment sample.

| Field | Verified value |
| --- | --- |
| Observation time | `2026-09-22T08:45:00Z` |
| Forecast time | `2026-09-22T10:11:00Z` |
| Collection time | `2026-09-22T08:57:31Z` |
| Path | `2026/09/22/20260922T084500Z.json` |
| Downloaded bytes | 918,185 |
| Total coordinate triples | 65,160 |
| Northern / southern / equator points | 32,400 / 32,400 / 360 |
| SHA-256 | `4ea0cd059f4c9c2cdc89ac3860fbab49a08a0f401d8968658f5cf792e7411151` |
| ETag | `"e02a9-65c0e83722de3"` |
| Last-Modified | `Tue, 22 Sep 2026 08:54:24 GMT` |

The saved file's SHA-256 matches its metadata. This verifies collection and storage locally, not a live scheduled service. The deployment verification below records the subsequent merge and first hosted collection; timer-triggered operation is a separate check.

## Deployment verification — 2026-09-22

- [PR #1](https://github.com/GefeiSHEN/NOAA-Aurora-Archive/pull/1) merged with user authorization at `2026-09-22T09:12:22Z`.
- Repository visibility is public; default branch is `main`; Actions is enabled. `Collect NOAA OVATION` is registered with state `active` and cron `2-59/5 * * * *`.
- [Manual collection run 35709058187](https://github.com/GefeiSHEN/NOAA-Aurora-Archive/actions/runs/35709058187) succeeded. The hosted Linux job passed all 15 tests and published data in 10 seconds.
- Data commit: `19284c1`. Observation `2026-09-22T09:04:00Z`, forecast `2026-09-22T10:32:00Z`, collection `2026-09-22T09:12:38Z`.
- Saved path: `2026/09/22/20260922T090400Z.json`. All 65,160 triples passed validation, and saved bytes matched metadata SHA-256 `1acbcc7b06e104cf98662fae25c860634c0379fd943cf060d2f4b8ef75b4db52`.
- At this check, no `schedule` event run had appeared. Manual hosted collection and publication are verified; automatic triggering is enabled but not yet observed.

## Automatic session recovery — 2026-09-22

The earlier deployment evidence was insufficient to establish a working five-minute cadence. Only one scheduled run appeared during the first eight hours: [35737401306](https://github.com/GefeiSHEN/NOAA-Aurora-Archive/actions/runs/35737401306), which published successfully at 14:01 UTC. Missing run records establish sparse triggering, not the internal reason GitHub omitted/delayed those events. Manual catch-ups were not automatic-run evidence.

[PR #2](https://github.com/GefeiSHEN/NOAA-Aurora-Archive/pull/2) introduced bounded automatic sessions and merged at 17:40:20 UTC. All 21 tests passed locally and in [hosted PR CI](https://github.com/GefeiSHEN/NOAA-Aurora-Archive/actions/runs/35762153039). The exact original cron and concurrency settings remain intact. Workflow registration was refreshed once and its state verified as active.

[Run 35762230816](https://github.com/GefeiSHEN/NOAA-Aurora-Archive/actions/runs/35762230816) started automatically from the deployment **push** at 17:40:23 UTC. It is not a cron-originated run or a manual dispatch. Its one-shot manual collection step was skipped, and the automatic session step remained in progress.

| Automatic collection time (UTC) | NOAA observation time (UTC) | Data commit |
| --- | --- | --- |
| 17:40:34 | 17:30:00 | `d9e526e` |
| 17:47:00 | 17:35:00 | `e76c9d2` |

The second publication occurred at 17:47:01 UTC with no additional dispatch. Public `latest.json` served `2026/09/22/20260922T173500Z.json`; its SHA-256 is `61d588f006f4dbf5aeac7e5ca6991a136ed5d547fbbfe1372765c7ca37808f31`. This verifies recurring collection inside the running session. A slot with identical NOAA bytes creates no commit.

At 17:47 UTC there was no queued scheduled successor. Session handoff and uninterrupted long-term coverage remain unverified. A session lasts up to 340 minutes plus completion of its final bounded collection; if GitHub fails to supply another run before it exits, a gap is still possible. The mitigation does not repair or guarantee GitHub's scheduler.
