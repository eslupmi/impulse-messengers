"""Slack API rejection must be recorded as failed delivery."""

import unittest
from unittest.mock import AsyncMock, Mock

from impulse_messenger_api import MessageRef, NotificationContent
from impulse_slack import SlackProvider
from impulse_slack.config import SlackApplicationConfig


class SlackNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_notification_checks_api_result_and_closes_response_once(self):
        provider = SlackProvider(
            SlackApplicationConfig(channels={}, users={}, admin_users=[]),
            {'SLACK_BOT_USER_OAUTH_TOKEN': 'synthetic-token', 'SLACK_VERIFICATION_TOKEN': 'verify'},
        )
        for status, body, expected in (
            (200, {'ok': True}, 200),
            (200, {'ok': False, 'error': 'channel_not_found'}, 502),
            (200, [], 502),
            (200, ValueError('synthetic-token reflected by decoder'), 502),
            (503, {'ok': False}, 503),
        ):
            with self.subTest(status=status, body=body):
                decode = AsyncMock(side_effect=body) if isinstance(body, Exception) else AsyncMock(return_value=body)
                response = Mock(status=status, json=decode)
                provider.http = Mock(post=AsyncMock(return_value=response))
                result = await provider.post_notification(MessageRef('C1', '123.456'), NotificationContent('notice'))
                self.assertEqual(result.status_code, expected)
                response.close.assert_called_once_with()
                if status != 200:
                    decode.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
