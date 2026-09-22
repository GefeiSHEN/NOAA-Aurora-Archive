#!/usr/bin/env python3
"""Byte-preserving NOAA OVATION collector. Python 3.11+, standard library only."""
import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SOURCE = 'https://services.swpc.noaa.gov/json/ovation_aurora_latest.json'
ARCHIVE_DIR = Path('OVATION')
UTC = timezone.utc
MAX_BYTES = 5 * 1024 * 1024
RETRY_DELAYS = (15, 30, 60)
MAX_RETRY_AFTER = 120


def utcnow():
    return datetime.now(UTC)


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})', value):
        raise ValueError('timestamp must include seconds and an explicit UTC offset')
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC)


def iso(value):
    if value.tzinfo is None:
        raise ValueError('timezone required')
    return value.astimezone(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def validate(body):
    if len(body) > MAX_BYTES:
        raise ValueError('response exceeds size limit')
    data = json.loads(body, object_pairs_hook=unique_keys)
    if not isinstance(data, dict) or data.get('type') != 'MultiPoint':
        raise ValueError('expected a MultiPoint object')
    if data.get('Data Format') != '[Longitude, Latitude, Aurora]':
        raise ValueError('unexpected coordinate format')
    try:
        observation = timestamp(data['Observation Time'])
        forecast = timestamp(data['Forecast Time'])
    except KeyError as exc:
        raise ValueError('missing NOAA timestamp') from exc
    if forecast < observation:
        raise ValueError('forecast precedes observation')
    coords = data.get('coordinates')
    if not isinstance(coords, list) or len(coords) != 360 * 181:
        raise ValueError('expected the complete 360 by 181 global grid')
    seen = set()
    north = south = False
    for point in coords:
        if not isinstance(point, list) or len(point) != 3:
            raise ValueError('coordinate must be a triple')
        if any(type(v) not in (int, float) or not -1e100 <= v <= 1e100
               or not math.isfinite(v) for v in point):
            raise ValueError('coordinate values must be finite numbers, not booleans')
        lon, lat, aurora = point
        if not (0 <= lon <= 359 and -90 <= lat <= 90 and 0 <= aurora <= 100):
            raise ValueError('coordinate or aurora value outside NOAA range')
        if int(lon) != lon or int(lat) != lat or (lon, lat) in seen:
            raise ValueError('off-grid or duplicate coordinate')
        seen.add((lon, lat))
        north |= lat > 0
        south |= lat < 0
    if not (north and south):
        raise ValueError('both hemispheres required')
    return {'observation_time': iso(observation), 'forecast_time': iso(forecast)}


def snapshot_path(forecast):
    return (ARCHIVE_DIR / timestamp(forecast).strftime(
        '%Y/%m/%d/%Y%m%dT%H%M%SZ.json')).as_posix()


def receipt(collected_at, headers):
    headers = {k.lower(): v for k, v in headers.items()}
    return {'collected_at': iso(collected_at), 'source_url': SOURCE,
            'http_validators': {k: headers[k] for k in ('etag', 'last-modified') if k in headers}}


def fetch_http():
    request = Request(SOURCE, headers={'User-Agent': 'NOAA-Ovation-Archive/1.0',
                                      'Accept': 'application/json', 'Accept-Encoding': 'identity'})
    # Do not use conditional requests: hash actual bytes, including upstream revisions.
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise ValueError(f'unexpected HTTP status {response.status}')
        body = response.read(MAX_BYTES + 1)
        length = response.headers.get('Content-Length')
        if length is not None and int(length) != len(body):
            raise ValueError('incomplete HTTP body')
        return body, dict(response.headers.items())


def retry_after(value, now):
    if not value:
        return 0
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        try:
            seconds = (parsedate_to_datetime(value) - now).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 0
    return max(0, min(MAX_RETRY_AFTER, seconds))


def download(fetch=fetch_http, sleep=time.sleep, clock=utcnow):
    for attempt in range(len(RETRY_DELAYS) + 1):
        delay = 0
        try:
            body, headers = fetch()
            collected = clock()
            validate(body)
            return body, receipt(collected, headers)
        except HTTPError as exc:
            if exc.code not in (408, 429) and not 500 <= exc.code <= 599:
                raise RuntimeError(f'non-retryable HTTP {exc.code}') from exc
            delay = retry_after(exc.headers.get('Retry-After'), clock())
            error = exc
        except (URLError, OSError, http.client.HTTPException, ValueError) as exc:
            error = exc
        if attempt == len(RETRY_DELAYS):
            raise RuntimeError(f'collection failed after {attempt + 1} attempts: {error}') from error
        delay = max(RETRY_DELAYS[attempt], delay)
        print(f'attempt {attempt + 1} failed: {error}; retry in {delay}s', file=sys.stderr)
        sleep(delay)


def read_json(path, default):
    return json.loads(path.read_bytes()) if path.exists() else default


def encode(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def atomic_write(path, body):
    if path.exists() and path.read_bytes() == body:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return True


def latest_key(entry):
    return entry['forecast_time'], entry['collected_at'], entry['sha256']


def recent_key(entry):
    return entry['collected_at'], entry['forecast_time'], entry['sha256']


def archive(root, body, received, now=None):
    """Single-writer operation. Validate/plan before writes; publish via one Git commit."""
    root = Path(root)
    now = now or utcnow()
    info = validate(body)  # Invalid downloads must not even prune the indexes.
    collected = timestamp(received['collected_at'])
    if received['source_url'] != SOURCE:
        raise ValueError('unexpected receipt source')
    digest = hashlib.sha256(body).hexdigest()
    base = snapshot_path(info['forecast_time'])
    # Forecast time is the canonical identity and metadata partition.
    archive_root = root / ARCHIVE_DIR
    metadata_path = root / Path(base).parent / 'metadata.json'
    metadata = read_json(metadata_path, {'schema_version': 1, 'snapshots': []})
    old_latest = read_json(archive_root / 'latest.json', {'schema_version': 1, 'snapshot': None})
    old_recent = read_json(archive_root / 'recent.json', {'schema_version': 1, 'window_hours': 24, 'snapshots': []})
    entries = metadata['snapshots']
    duplicate = next((e for e in entries if e['sha256'] == digest), None)
    pending = []
    if duplicate:
        if (root / duplicate['path']).read_bytes() != body:
            raise ValueError('archived bytes disagree with metadata')
        entry = duplicate  # Do not refresh first collection time or validators.
    else:
        path = base
        if (root / base).exists():
            path = base[:-5] + '-' + digest + '.json'
        if (root / path).exists() and (root / path).read_bytes() != body:
            raise ValueError('refusing to overwrite an existing snapshot')
        entry = {**info, 'collected_at': iso(collected), 'source_url': SOURCE,
                 'http_validators': received['http_validators'], 'sha256': digest, 'path': path}
        entries.append(entry)
        entries.sort(key=latest_key, reverse=True)
        pending.extend([(root / path, body), (metadata_path, encode(metadata))])
    candidates = [e for e in (old_latest['snapshot'], entry) if e is not None]
    latest = {'schema_version': 1, 'snapshot': max(candidates, key=latest_key)}
    recent_entries = {e['path']: e for e in old_recent['snapshots']}
    recent_entries[entry['path']] = entry
    cutoff = now - timedelta(hours=24)
    recent = {'schema_version': 1, 'window_hours': 24,
              'snapshots': sorted((e for e in recent_entries.values()
                                   if cutoff <= timestamp(e['collected_at']) <= now),
                                  key=recent_key, reverse=True)}
    pending.extend([(archive_root / 'latest.json', encode(latest)),
                    (archive_root / 'recent.json', encode(recent))])
    changed = False
    for path, content in pending:
        changed = atomic_write(path, content) or changed
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    args = parser.parse_args()
    body, received = download()
    changed = archive(args.root, body, received)
    print('archive updated' if changed else 'unchanged: successful no-op')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError) as exc:
        sys.exit(f'ERROR: {exc}')
