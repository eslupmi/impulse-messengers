"""Pachca wire protocol, payloads and packaged templates."""
import hashlib
import hmac
import json
import logging
import time
from importlib.resources import files

from impulse_messenger_api import (
    DeliveryResult, GroupProfile, IncidentPresentation, Interaction, InteractionAction,
    InteractionCommand, InteractionRequest, MessageRef, NotificationContent, ProviderContext,
    ProviderDescriptor, ProviderIdentity, ProviderResponse, REQUIRED_TEMPLATE_NAMES, SecretResolver, UserProfile,
)
from .buttons import buttons
from .config import PachcaApplicationConfig

logger = logging.getLogger('main_logger')

API_BASE = 'https://api.pachca.com/api/shared/v1'
APP_URL = 'https://app.pachca.com'
WEBHOOK_MAX_AGE_SECONDS = 60
COMMANDS = {
    'stop_chain': InteractionCommand(InteractionAction.ASSIGN),
    'start_chain': InteractionCommand(InteractionAction.RELEASE),
    'task': InteractionCommand(InteractionAction.CREATE_TASK),
    'freeze_menu': InteractionCommand(InteractionAction.SHOW_FREEZE_OPTIONS),
    'unfreeze': InteractionCommand(InteractionAction.UNFREEZE),
    'freeze_back': InteractionCommand(InteractionAction.NOOP),
    'noop': InteractionCommand(InteractionAction.NOOP),
    'freeze_tomorrow': InteractionCommand(InteractionAction.FREEZE, 'tomorrow'),
    'freeze_next_monday': InteractionCommand(InteractionAction.FREEZE, 'next_monday'),
    'freeze_month': InteractionCommand(InteractionAction.FREEZE, 'month'),
    'freeze_6months': InteractionCommand(InteractionAction.FREEZE, '6months'),
}
TEMPLATE_NAMES = frozenset(REQUIRED_TEMPLATE_NAMES)


class PachcaProvider:
    descriptor = ProviderDescriptor('pachca', rate_limit=4, rate_window_seconds=1.0, notification_headers=False)
    config_model = PachcaApplicationConfig

    def __init__(self, config: PachcaApplicationConfig, secrets: SecretResolver):
        token = secrets.get('PACHCA_BOT_TOKEN')
        if not token:
            raise ValueError('PACHCA_BOT_TOKEN is required')
        self._signing_secret = secrets.get('PACHCA_SIGNING_SECRET')
        if not self._signing_secret:
            raise ValueError('PACHCA_SIGNING_SECRET is required')
        self.url = API_BASE
        self.team = None
        self.callback_url = None
        self._groups = tuple(group.id for group in config.groups.values())
        self.headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}

    async def initialize(self, context: ProviderContext) -> ProviderIdentity:
        self.http = context.http
        self.callback_url = context.callback_url
        return ProviderIdentity(APP_URL)

    async def activate(self) -> None:
        response = await self.http.put(f'{self.url}/bot/webhook', headers=self.headers,
                                       json={'webhook': {'outgoing_url': self.callback_url}})
        self._check(response, 'webhook registration')

    @staticmethod
    async def _read_json(response):
        try:
            return await response.json()
        finally:
            response.close()

    @staticmethod
    def _check(response, operation):
        try:
            if not 200 <= response.status < 300:
                raise RuntimeError(f'Pachca {operation} failed (HTTP {response.status})')
        finally:
            response.close()

    async def fetch_user(self, user_id: str | int) -> UserProfile:
        response = await self.http.get(f'{self.url}/users/{user_id}', headers=self.headers)
        status = response.status
        if status != 200:
            response.close()
            logger.warning('User details fetch failed', extra={'user_id': user_id, 'status': status})
            return UserProfile(id=user_id, exists=False)
        data = (await self._read_json(response))['data']
        full_name = f"{data.get('first_name') or ''} {data.get('last_name') or ''}".strip()
        return UserProfile(id=user_id, exists=True, full_name=full_name, username=data.get('nickname'),
                           email=data.get('email'), timezone=data.get('time_zone'))

    async def fetch_groups(self) -> tuple[GroupProfile, ...]:
        profiles = []
        for group_id in self._groups:
            response = await self.http.get(f'{self.url}/group_tags/{group_id}', headers=self.headers)
            status = response.status
            if status != 200:
                response.close()
                logger.warning('Group details fetch failed', extra={'group_id': group_id, 'status': status})
                profiles.append(GroupProfile(id=group_id, name=None, exists=False))
            else:
                profiles.append(GroupProfile(id=group_id, name=(await self._read_json(response))['data']['name']))
        return tuple(profiles)

    @staticmethod
    def _content(message: IncidentPresentation) -> str:
        return f'{message.status_icon} {message.header}\n{message.body}'

    @staticmethod
    def _freeze_button(message: IncidentPresentation):
        if message.frozen_by_maintenance:
            return {'text': 'Maintenance', 'data': 'noop'}
        if message.frozen_by_inhibition:
            return buttons['freeze']['inhibited']
        if message.can_unfreeze:
            return {'text': message.frozen_until_text, 'data': 'unfreeze'}
        if message.frozen_until:
            return {'text': message.frozen_until_text, 'data': 'noop'}
        return buttons['freeze']['inactive']

    @classmethod
    def _buttons(cls, message: IncidentPresentation, freeze_menu=False):
        if freeze_menu:
            return [[option] for option in buttons['freeze']['options']]
        if message.status == 'closed':
            return []
        release = not message.chain_enabled and message.status == 'resolved'
        row = [buttons['chain']['release' if release else 'takeit'], cls._freeze_button(message)]
        if message.can_create_task and not message.task_link:
            row.append(buttons['task']['create'])
        return [row]

    async def create_incident(self, message: IncidentPresentation) -> MessageRef | None:
        response = await self.http.post(f'{self.url}/messages', headers=self.headers, json={'message': {
            'entity_id': message.channel_id, 'content': self._content(message), 'buttons': self._buttons(message),
        }})
        status = response.status
        if not 200 <= status < 300:
            response.close()
            logger.error('Incident message creation failed', extra={'messenger': 'pachca', 'status': status})
            return None
        return MessageRef(message.channel_id, str((await self._read_json(response))['data']['id']))

    async def update_incident(self, message: IncidentPresentation, *, freeze_menu=False) -> None:
        response = await self.http.put(f'{self.url}/messages/{message.thread_id}', headers=self.headers, json={
            'message': {'content': self._content(message), 'buttons': self._buttons(message, freeze_menu)},
        })
        self._check(response, 'incident update')

    async def post_notification(self, message: MessageRef, content: NotificationContent) -> DeliveryResult:
        response = await self.http.post(f'{self.url}/messages/{message.thread_id}/thread', headers=self.headers)
        if not 200 <= response.status < 300:
            response.close()
            return DeliveryResult(response.status)
        thread_id = (await self._read_json(response))['data']['id']
        response = await self.http.post(f'{self.url}/messages', headers=self.headers, json={
            'message': {'entity_type': 'thread', 'entity_id': thread_id, 'content': content.text},
        })
        response.close()
        return DeliveryResult(response.status)

    async def parse_interaction(self, request: InteractionRequest) -> Interaction | ProviderResponse:
        headers = {key.lower(): value for key, value in request.headers}
        signature = hmac.new(self._signing_secret.encode(), request.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature.encode(), headers.get('pachca-signature', '').encode()):
            return ProviderResponse(status_code=401)
        try:
            payload = json.loads(request.body)
            if not isinstance(payload, dict):
                return ProviderResponse(status_code=400)
            timestamp = payload['webhook_timestamp']
            if type(timestamp) not in (int, float):
                return ProviderResponse(status_code=400)
            now = time.time()
            if not now - WEBHOOK_MAX_AGE_SECONDS <= timestamp <= now + WEBHOOK_MAX_AGE_SECONDS:
                return ProviderResponse(status_code=401)
            if payload.get('type') != 'button':
                return ProviderResponse()
            if any(type(payload[key]) is not int or payload[key] <= 0 for key in ('chat_id', 'message_id', 'user_id')):
                return ProviderResponse(status_code=400)
            return Interaction(MessageRef(payload['chat_id'], str(payload['message_id'])), payload['user_id'],
                               (COMMANDS[payload['data']],))
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
            return ProviderResponse(status_code=400)

    async def after_interaction(self, message: IncidentPresentation, interaction: Interaction) -> None:
        freeze_menu = any(command.action == InteractionAction.SHOW_FREEZE_OPTIONS for command in interaction.commands)
        await self.update_incident(message, freeze_menu=freeze_menu)

    @staticmethod
    def respond_to_interaction(message: IncidentPresentation) -> ProviderResponse:
        return ProviderResponse()

    @staticmethod
    def incident_url(message: MessageRef, identity: ProviderIdentity) -> str:
        return f'{identity.public_url}/chats/{message.channel_id}?message={message.thread_id}'

    @staticmethod
    def user_url(user: UserProfile, identity: ProviderIdentity) -> str | None:
        return None

    @staticmethod
    def mention_id(user: UserProfile) -> str | None:
        return user.username

    @staticmethod
    def template_source(name: str) -> str:
        if name not in TEMPLATE_NAMES:
            raise KeyError(name)
        return files(__package__).joinpath('resources').joinpath(name + '.j2').read_text(encoding='utf-8')
