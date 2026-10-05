"""Telegram response decoding never exposes token-bearing request URLs."""

import asyncio
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from aiohttp import ClientConnectionError, ContentTypeError, ServerTimeoutError
from impulse_messenger_api import IncidentPresentation
from impulse_telegram import TelegramProvider
from impulse_telegram.config import TelegramApplicationConfig


class TelegramResponseErrorsTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_and_creation_decode_errors_are_safe_and_close_response(self):
        token = 'synthetic-secret-token'
        provider = TelegramProvider(
            TelegramApplicationConfig(channels={}, users={}, admin_users=[], impulse_address='https://impulse.test'),
            {'TELEGRAM_BOT_TOKEN': token},
        )
        message = IncidentPresentation(-100123, None, 'firing', 'header', 'body', 'icon',
                                       True, False, False, False, None, False, '')
        for operation in (lambda: provider.fetch_user(123), lambda: provider.create_incident(message)):
            with self.subTest(operation=operation):
                error = ContentTypeError(
                    SimpleNamespace(real_url=f'{provider.url}/getChat'), (),
                    status=200, message='Attempt to decode JSON with unexpected mimetype: text/html',
                )
                self.assertIn(token, str(error))
                response = Mock(status=200, json=AsyncMock(side_effect=error))
                provider.http = Mock(get=AsyncMock(return_value=response), post=AsyncMock(return_value=response))
                try:
                    await operation()
                except ValueError as caught:
                    self.assertIn('invalid JSON', str(caught))
                    self.assertIn('HTTP 200', str(caught))
                    self.assertNotIn(token, ''.join(traceback.format_exception(caught)))
                else:
                    self.fail('Invalid JSON must fail without exposing the request URL')
                response.close.assert_called_once_with()

    async def test_transport_errors_keep_their_classification_without_exposing_credentials(self):
        token = 'synthetic-secret-token'
        for error_type, expected_type in (
            (asyncio.TimeoutError, asyncio.TimeoutError),
            (ServerTimeoutError, asyncio.TimeoutError),
            (ClientConnectionError, ClientConnectionError),
        ):
            with self.subTest(error_type=error_type):
                response = Mock(status=200, json=AsyncMock(side_effect=error_type(token)))
                with self.assertRaises(expected_type) as caught:
                    await TelegramProvider._read_json(response)
                self.assertNotIn(token, ''.join(traceback.format_exception(caught.exception)))
                response.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
