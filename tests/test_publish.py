from datetime import timedelta
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts import collect, publish
from test_collect import NOW, fixture, raw


class PublicationTests(unittest.TestCase):
    def test_conflicting_writer_keeps_both_snapshots_and_user_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote, repo, other = (root / n for n in ('remote', 'repo', 'other'))
            def git(path, *args):
                return publish.git(path, *args)
            subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
            subprocess.run(['git', 'init', '-b', 'main', str(repo)], check=True, capture_output=True)
            git(repo, 'config', 'user.name', 'Test')
            git(repo, 'config', 'user.email', 'test@example.invalid')
            (repo / 'README.md').write_text('baseline')
            git(repo, 'add', '.')
            git(repo, 'commit', '-m', 'chore(repo): baseline')
            git(repo, 'remote', 'add', 'origin', str(remote))
            git(repo, 'push', '-u', 'origin', 'main')
            subprocess.run(['git', 'clone', '-b', 'main', str(remote), str(other)],
                           check=True, capture_output=True)
            git(other, 'config', 'user.name', 'Test')
            git(other, 'config', 'user.email', 'test@example.invalid')
            original_head = git(repo, 'rev-parse', 'HEAD')
            (repo / 'README.md').write_text('unsaved user changes')
            def conflict(attempt):
                if attempt == 0:
                    collect.archive(other, raw(fixture('2026-09-22T01:00:00Z')),
                                    collect.receipt(NOW + timedelta(minutes=5), {}))
                    git(other, 'add', '.')
                    git(other, 'commit', '-m', 'data(ovation): competing snapshot')
                    git(other, 'push', 'origin', 'main')
            self.assertTrue(publish.publish(repo, 'main', raw(), collect.receipt(NOW, {}),
                                            sleep=lambda _: None, before_push=conflict))
            git(other, 'pull', '--ff-only')
            self.assertTrue((other / '2026/09/22/20260922T005500Z.json').exists())
            self.assertTrue((other / '2026/09/22/20260922T010000Z.json').exists())
            latest = collect.read_json(other / 'latest.json', {})['snapshot']
            self.assertEqual(latest['observation_time'], '2026-09-22T01:00:00Z')
            self.assertEqual(git(repo, 'rev-parse', 'HEAD'), original_head)
            self.assertEqual((repo / 'README.md').read_text(), 'unsaved user changes')
            self.assertFalse(publish.publish(repo, 'main', raw(), collect.receipt(NOW, {})))


if __name__ == '__main__':
    unittest.main()
