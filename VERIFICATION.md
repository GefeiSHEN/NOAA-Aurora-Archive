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

## Six-minute debugging — 2026-09-22

[PR #3](https://github.com/GefeiSHEN/NOAA-Aurora-Archive/pull/3) shortened sessions to six minutes, with an eight-minute collection-job safety cap. The obsolete long session was canceled so it could not block the debug run.

[Run 35767726079](https://github.com/GefeiSHEN/NOAA-Aurora-Archive/actions/runs/35767726079) completed successfully: job start `18:32:05Z`, finish `18:38:19Z`, elapsed **6 minutes 14 seconds** including setup/cleanup. No successor was queued or running at completion. This confirmed that shortening a run did not itself supply automatic continuation.

[PR #4](https://github.com/GefeiSHEN/NOAA-Aurora-Archive/pull/4) added an explicit native Actions handoff after a successful collection. The handoff uses the built-in token in a separate `actions: write` job; there is no personal token or external scheduler. The exact original cron remains a recovery trigger. Hosted checks passed all 21 tests. The successor run's event, actor, source run ID, and start time must be checked independently from a successful dispatch request.

### Verified automatic successor

- Source [35768554781](https://github.com/GefeiSHEN/NOAA-Aurora-Archive/actions/runs/35768554781): collection job `18:38:58Z`–`18:45:09Z` (**6 minutes 11 seconds**); handoff job `18:45:11Z`–`18:45:15Z`. Both succeeded.
- Successor [35769270006](https://github.com/GefeiSHEN/NOAA-Aurora-Archive/actions/runs/35769270006): created `18:45:14Z`, collection job started `18:45:18Z`, actor **github-actions[bot]**, event **workflow_dispatch**, title `NOAA OVATION — workflow_dispatch (handoff from 35768554781) — main`.
- The successor passed all 21 tests, entered collection, and published commit `f54ec6d` with collection time `18:45:26Z`, observation `18:35:00Z`, forecast `20:05:00Z`, and SHA-256 `cadee7cc4da361876b4f09f2d09c28a08ecab0d4daaa5f7664ce178c1c3522a8`.
- No user/agent manual dispatch started this successor. Its predecessor requested it using the built-in Actions token. This verifies one full automatic handoff and data publication by the successor; it does not establish a reliability guarantee for future outages.

The six-minute debug duration remains deployed. Successful sessions continue the native handoff chain; failed or canceled sessions do not. Cron remains enabled as recovery. Disable the workflow and cancel active/pending runs to stop collection.
