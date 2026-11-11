"""Exercise queue pagination and publication without GitHub requests."""
import contextlib
import datetime
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


SOURCE = Path(__file__).resolve().parents[1] / 'github/queue.py'
SPEC = importlib.util.spec_from_file_location('agent_queue_pagination', SOURCE)
queue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(queue)


class QueuePaginationTests(unittest.TestCase):
    def test_complete_snapshot_or_no_publication_at_page_limit(self):
        now = datetime.datetime(2026, 11, 11, 12, tzinfo=datetime.timezone.utc)
        issue = {'number': 1, 'title': 'Fixture', 'state': 'open',
                 'html_url': 'https://example.test/1'}
        for count in (9999, 10000, 12000):
            for existing in (False, True):
                with self.subTest(comments=count, existing=existing), tempfile.TemporaryDirectory() as directory:
                    output = Path(directory) / 'queue'
                    summary = Path(directory) / 'summary.md'
                    previous = b'previous snapshot\n'
                    if existing:
                        output.mkdir()
                        for name in ('queue.json', 'queue.md'):
                            (output / name).write_bytes(previous)
                        summary.write_bytes(previous)
                    comment_pages = []

                    def urlopen(request, timeout):
                        parsed = urlsplit(request.full_url)
                        query = parse_qs(parsed.query)
                        page = int(query['page'][0])
                        self.assertEqual(query['per_page'], ['100'])
                        if parsed.path.endswith('/comments'):
                            self.assertEqual(query['since'], ['2026-10-28T12:00:00Z'])
                            comment_pages.append(page)
                            batch = [{'issue_url': 'https://api.github.com/repos/o/r/issues/1',
                                      'body': 'Agent: fixture\nTask: #1\nOwned paths: docs/x.md\nState: working',
                                      'user': {'login': 'fixture'},
                                      'html_url': f'https://example.test/c{index:05}',
                                      'created_at': now.isoformat(), 'updated_at': now.isoformat()}
                                     for index in range((page - 1) * 100, min(page * 100, count))]
                        else:
                            self.assertEqual(page, 1)
                            batch = [issue]
                        return io.StringIO(json.dumps(batch))

                    with patch.object(queue.urllib.request, 'urlopen', side_effect=urlopen), \
                         patch.object(queue.datetime, 'datetime', wraps=datetime.datetime) as clock, \
                         patch.dict(os.environ, {'GITHUB_REPOSITORY': 'o/r', 'GITHUB_TOKEN': 'fixture',
                                                 'GITHUB_STEP_SUMMARY': str(summary)}, clear=True), \
                         patch.object(sys, 'argv', ['queue.py', '--output', str(output)]), \
                         contextlib.redirect_stdout(io.StringIO()) as stdout:
                        clock.now.return_value = now
                        if count == 9999:
                            queue.main()
                        else:
                            with self.assertRaisesRegex(RuntimeError, 'no partial snapshot published'):
                                queue.main()
                    self.assertEqual(comment_pages, list(range(1, 101)))
                    if count == 9999:
                        data = json.loads((output / 'queue.json').read_text())
                        self.assertEqual(len(data['claims'][0]['history']), count)
                        self.assertIn('Published 1 tasks and 1 named check-ins.', stdout.getvalue())
                        self.assertTrue((output / 'queue.md').is_file())
                        self.assertIn((output / 'queue.md').read_text(), summary.read_text())
                    else:
                        self.assertEqual(stdout.getvalue(), '')
                        if existing:
                            for path in (output / 'queue.json', output / 'queue.md', summary):
                                self.assertEqual(path.read_bytes(), previous)
                        else:
                            self.assertFalse(output.exists())
                            self.assertFalse(summary.exists())


if __name__ == '__main__':
    unittest.main()
