"""Pause writers, then run python3 -m agents.move_day_metadata to colocate metadata."""
import hashlib
import json
from pathlib import Path
import re


def migrate(root):
    root = Path(root)
    legacy = root / 'OVATION/metadata'
    plans = []
    for source in sorted(legacy.rglob('*.json')):
        day = source.relative_to(legacy).as_posix()
        if not re.fullmatch(r'\d{4}/\d{2}/\d{2}\.json', day):
            raise ValueError(f'unexpected metadata path: {source}')
        target = root / 'OVATION' / day[:-5] / 'metadata.json'
        if target.exists():
            raise FileExistsError(f'refusing to overwrite {target}')
        index = json.loads(source.read_bytes())
        if index.get('schema_version') != 1:
            raise ValueError('unexpected metadata schema')
        for entry in index['snapshots']:
            path = Path(entry['path'])
            if path.parent != target.parent.relative_to(root):
                raise ValueError(f'snapshot belongs to another day: {path}')
            if hashlib.sha256((root / path).read_bytes()).hexdigest() != entry['sha256']:
                raise ValueError(f'snapshot hash mismatch: {path}')
        plans.append((source, target))
    for source, target in plans:
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
    if legacy.exists():
        directories = sorted((p for p in legacy.rglob('*') if p.is_dir()),
                             key=lambda p: len(p.parts), reverse=True)
        for directory in directories + [legacy]:
            if not any(directory.iterdir()):
                directory.rmdir()
    return len(plans)


if __name__ == '__main__':
    print(f'Moved {migrate(Path("."))} metadata indexes into their day folders.')
