"""The package probe rejects release mismatches and unexpected requests."""

from pathlib import Path
import runpy
import unittest
from unittest.mock import patch


harness = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / 'scripts' / 'verify-packages.py')
)
FakeTransport = harness['FakeTransport']


class ReleaseVersionTests(unittest.TestCase):
    def test_rejects_mismatched_installed_release_versions(self):
        versions = {'impulse-bot': '3.8.0', 'impulse-slack': '3.7.1'}
        with patch('importlib.metadata.version', side_effect=versions.__getitem__):
            with self.assertRaisesRegex(AssertionError, 'Release version mismatch'):
                harness['probe']('slack')


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_unexpected_requests(self):
        transport = FakeTransport('slack')
        for method, url in (
            ('PUT', 'https://slack.com/api/chat.postMessage'),
            ('POST', 'https://slack.com/api/chat.postMessages'),
            ('POST', 'https://wrong-host.test/api/chat.postMessage'),
            ('POST', 'https://mattermost.test/api/v4/posts'),
        ):
            with self.subTest(method=method, url=url):
                with self.assertRaisesRegex(AssertionError, 'Unexpected provider request'):
                    await transport.request(method, url)
        self.assertEqual(transport.responses, [])


if __name__ == '__main__':
    unittest.main()
