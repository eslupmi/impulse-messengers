"""The package probe must reject requests outside its explicit fixtures."""

from pathlib import Path
import runpy
import unittest


FakeTransport = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / 'scripts' / 'verify-packages.py')
)['FakeTransport']


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
