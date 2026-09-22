# Initial verification — 2026-09-22

- `python3 -m unittest discover -s tests -v`: **15 tests passed** on Python 3.12 locally. Fixtures are generated in tests and never enter the real archive.
- Integration test uses a local bare Git remote and a competing writer to force a rejected push. Retrying preserves both snapshots, retains the newer latest pointer, and leaves the original checkout and its uncommitted file untouched. Publishing the same bytes again makes no commit.
- Both workflow YAML files parse successfully. Hosted workflow execution is checked separately in the implementation PR.
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

The saved file's SHA-256 matches its metadata. This verifies collection and storage locally, not a live scheduled service. The implementation must be merged into the public default branch with Actions enabled and a successful scheduled run observed before scheduled collection can be described as live.
