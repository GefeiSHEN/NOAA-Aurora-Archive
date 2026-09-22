"""One-time layout migration. Pause all writers; run python3 -m agents.migrate_ovation."""
import argparse
from collections import defaultdict
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import re

from scripts import collect


def migrate(root, now=None):
    root = Path(root)
    now = now or collect.utcnow()
    years = [p for p in root.iterdir() if p.is_dir() and re.fullmatch(r'\d{4}', p.name)]
    payloads = {p.relative_to(root).as_posix(): p for year in years for p in year.rglob('*.json')}
    indexes = sorted((root / 'metadata').rglob('*.json'))
    indexes += [root / name for name in ('latest.json', 'recent.json') if (root / name).exists()]
    if not payloads and not indexes:
        return 0
    if not all((root / name).is_file() for name in ('latest.json', 'recent.json')):
        raise ValueError('legacy latest/recent indexes are missing')

    # Validate the complete migration plan before changing any files.
    hashes = {}
    info = {}
    for name, path in payloads.items():
        if path.is_symlink():
            raise ValueError('refusing to migrate a symlink')
        body = path.read_bytes()
        hashes[name] = hashlib.sha256(body).hexdigest()
        info[name] = collect.validate(body)
    records = {}
    for index_path in indexes:
        index = json.loads(index_path.read_bytes())
        if index.get('schema_version') != 1:
            raise ValueError('unexpected index schema')
        entries = [index['snapshot']] if 'snapshot' in index else index['snapshots']
        for entry in entries:
            name = entry['path']
            if name not in payloads:
                raise ValueError(f'index points outside the legacy payload set: {name}')
            if entry['sha256'] != hashes[name]:
                raise ValueError(f'hash mismatch: {name}')
            if any(entry[key] != value for key, value in info[name].items()):
                raise ValueError(f'timestamp mismatch: {name}')
            collect.timestamp(entry['collected_at'])
            old_base = collect.timestamp(entry['observation_time']).strftime('%Y/%m/%d/%Y%m%dT%H%M%SZ.json')
            if name not in (old_base, old_base[:-5] + '-' + hashes[name] + '.json'):
                raise ValueError(f'noncanonical legacy observation path: {name}')
            if index_path.is_relative_to(root / 'metadata'):
                if index_path != root / 'metadata' / (name[:10] + '.json'):
                    raise ValueError(f'metadata is in the wrong observation-day shard: {name}')
                if name in records:
                    raise ValueError(f'duplicate metadata entry: {name}')
                records[name] = entry
    if set(records) != set(payloads):
        raise ValueError('legacy metadata does not account for every payload')

    moves = []
    groups = defaultdict(list)
    used = set()
    forecasts = set()
    entries = []
    # Earliest collected bytes keep the canonical name for each forecast time.
    for old in sorted(records.values(), key=lambda e: (e['collected_at'], e['sha256'])):
        forecast = old['forecast_time']
        base = collect.snapshot_path(forecast)
        name = base if forecast not in forecasts else base[:-5] + '-' + old['sha256'] + '.json'
        if name in used:
            raise ValueError(f'duplicate forecast/hash identity: {name}')
        used.add(name)
        forecasts.add(forecast)
        entry = {**old, 'path': name}
        entries.append(entry)
        moves.append((payloads[old['path']], root / name))
        groups[root / Path(name).parent / 'metadata.json'].append(entry)
    plans = [(path, collect.encode({'schema_version': 1, 'snapshots':
              sorted(items, key=collect.latest_key, reverse=True)})) for path, items in groups.items()]
    plans += [(root / 'OVATION/latest.json', collect.encode(
        {'schema_version': 1, 'snapshot': max(entries, key=collect.latest_key)})),
        (root / 'OVATION/recent.json', collect.encode({'schema_version': 1, 'window_hours': 24,
         'snapshots': sorted((e for e in entries if now - timedelta(hours=24) <=
                              collect.timestamp(e['collected_at']) <= now),
                             key=collect.recent_key, reverse=True)}))]
    destinations = [target for _, target in moves] + [target for target, _ in plans]
    for target in destinations:
        if target.exists():
            raise FileExistsError(f'refusing to overwrite {target}')

    for source, target in moves:
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)  # No serialization or coordinate/value transformation.
    for target, body in plans:
        collect.atomic_write(target, body)
    for index_path in indexes:
        index_path.unlink()
    # Remove only empty legacy directories; unrelated files are preserved.
    for directory in years + [root / 'metadata']:
        children = sorted((p for p in directory.rglob('*') if p.is_dir()),
                          key=lambda p: len(p.parts), reverse=True)
        for child in children + [directory]:
            if child.exists() and not any(child.iterdir()):
                child.rmdir()
    return len(payloads)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    args = parser.parse_args()
    print(f'Migrated {migrate(args.root)} byte-identical snapshots into OVATION/.')
