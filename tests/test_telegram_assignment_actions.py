import json
import unittest

from impulse_messenger_api import InteractionAction, InteractionRequest
from impulse_telegram import TelegramProvider
from impulse_telegram.config import TelegramApplicationConfig


class TelegramAssignmentActionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_buttons_preserve_claim_and_release_commands(self):
        provider = TelegramProvider(
            TelegramApplicationConfig(channels={}, users={}, admin_users=[], impulse_address='https://impulse.test'),
            {'TELEGRAM_BOT_TOKEN': 'synthetic-token'},
        )
        for button, action in [('stop_chain', InteractionAction.ASSIGN), ('start_chain', InteractionAction.RELEASE)]:
            with self.subTest(button=button):
                payload = {'callback_query': {'id': 'ack', 'from': {'id': 123}, 'data': button,
                           'message': {'message_id': 20, 'message_thread_id': 10, 'chat': {'id': -100123}}}}
                interaction = await provider.parse_interaction(InteractionRequest('POST', (), (), json.dumps(payload).encode()))
                self.assertEqual(interaction.commands[0].action, action)
