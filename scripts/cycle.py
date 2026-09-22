"""Plan one collection window across five standard GitHub-hosted jobs."""
import argparse
from datetime import datetime, timezone
import math

CYCLE_SECONDS = 24 * 3600 - 30
SEGMENTS = 5


def plan(seconds=CYCLE_SECONDS, now=None):
    if type(seconds) is not int or not 30 <= seconds <= CYCLE_SECONDS:
        raise ValueError(f'cycle_seconds must be an integer from 30 to {CYCLE_SECONDS}')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('timezone required')
    return {'deadline': now.timestamp() + seconds,
            'segment_seconds': math.ceil(seconds / SEGMENTS)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=CYCLE_SECONDS)
    args = parser.parse_args()
    for key, value in plan(args.seconds).items():
        print(f'{key}={value}')
