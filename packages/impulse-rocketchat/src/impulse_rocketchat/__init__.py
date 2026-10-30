"""Rocket.Chat REST messages and native outgoing integration callbacks."""
from datetime import datetime, timezone
import hmac
from importlib.resources import files
import json
import logging
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from impulse_messenger_api import (
    DeliveryResult, GroupProfile, IncidentPresentation, Interaction, InteractionAction,
    InteractionCommand, InteractionRequest, MessageRef, NotificationContent, ProviderContext,
    ProviderDescriptor, ProviderIdentity, ProviderResponse, REQUIRED_TEMPLATE_NAMES, SecretResolver, UserProfile,
)

from .config import RocketChatApplicationConfig

logger = logging.getLogger('main_logger')

COMMAND_PREFIX = 'impulse'
CALLBACK_MAX_AGE_SECONDS = 300
COMMANDS = {
    'chain': InteractionCommand(InteractionAction.TOGGLE_ASSIGNMENT),
    'task': InteractionCommand(InteractionAction.CREATE_TASK),
    'freeze_menu': InteractionCommand(InteractionAction.SHOW_FREEZE_OPTIONS),
    'freeze_back': InteractionCommand(InteractionAction.NOOP),
    'noop': InteractionCommand(InteractionAction.NOOP),
    'unfreeze': InteractionCommand(InteractionAction.UNFREEZE),
    'freeze_tomorrow': InteractionCommand(InteractionAction.FREEZE, 'tomorrow'),
    'freeze_next_monday': InteractionCommand(InteractionAction.FREEZE, 'next_monday'),
    'freeze_month': InteractionCommand(InteractionAction.FREEZE, 'month'),
    'freeze_6months': InteractionCommand(InteractionAction.FREEZE, '6months'),
}
FREEZE_OPTIONS = (
    ('Tomorrow', 'freeze_tomorrow'), ('Next Monday', 'freeze_next_monday'),
    ('In Month', 'freeze_month'), ('In 6 months', 'freeze_6months'), ('« Back', 'freeze_back'),
)
TEMPLATE_NAMES = frozenset(REQUIRED_TEMPLATE_NAMES)


class RocketChatProvider:
    descriptor = ProviderDescriptor('rocketchat', rate_limit=1, rate_window_seconds=1.0)
    config_model = RocketChatApplicationConfig

    def __init__(self, config: RocketChatApplicationConfig, secrets: SecretResolver):
        token = secrets.get('ROCKETCHAT_AUTH_TOKEN')
        user_id = secrets.get('ROCKETCHAT_USER_ID')
        self._webhook_token = secrets.get('ROCKETCHAT_WEBHOOK_TOKEN')
        for name, value in (('ROCKETCHAT_AUTH_TOKEN', token), ('ROCKETCHAT_USER_ID', user_id),
                            ('ROCKETCHAT_WEBHOOK_TOKEN', self._webhook_token)):
            if not value:
                raise ValueError(f'{name} is required')
        self.url = config.address.rstrip('/')
        self.team = None
        self._channels = frozenset(channel.id for channel in config.channels.values())
        self._groups = tuple(group.id for group in config.groups.values())
        self.headers = {'Content-Type': 'application/json', 'X-Auth-Token': token, 'X-User-Id': user_id}

    async def initialize(self, context: ProviderContext) -> ProviderIdentity:
        self.http = context.http
        return ProviderIdentity(self.url)

    async def activate(self) -> None:
        pass

    @staticmethod
    async def _read(response):
        try:
            return await response.json()
        finally:
            response.close()

    async def fetch_user(self, user_id: str | int) -> UserProfile:
        response = await self.http.get(f'{self.url}/api/v1/users.info', headers=self.headers,
                                       params={'userId': user_id})
        status = response.status
        if status != 200:
            response.close()
            logger.warning('User details fetch failed', extra={'user_id': user_id, 'status': status})
            return UserProfile(user_id, exists=False)
        data = await self._read(response)
        if not data.get('success'):
            return UserProfile(user_id, exists=False)
        user = data['user']
        emails = user.get('emails') or []
        return UserProfile(user_id, exists=bool(user.get('active', True)), full_name=user.get('name'),
                           username=user.get('username'), email=emails[0].get('address') if emails else None)

    async def fetch_groups(self) -> tuple[GroupProfile, ...]:
        profiles = []
        for group_id in self._groups:
            response = await self.http.get(f'{self.url}/api/v1/teams.info', headers=self.headers,
                                           params={'teamId': group_id})
            status = response.status
            if status != 200:
                response.close()
                logger.warning('Group details fetch failed', extra={'group_id': group_id, 'status': status})
                profiles.append(GroupProfile(group_id, None, exists=False))
                continue
            data = await self._read(response)
            profiles.append(GroupProfile(group_id, data.get('teamInfo', {}).get('name'),
                                         exists=bool(data.get('success'))))
        return tuple(profiles)

    @staticmethod
    def _content(message: IncidentPresentation) -> str:
        text = f'{message.status_icon} {message.header}\n{message.body}'
        if message.frozen_by_maintenance:
            text += '\nMaintenance'
        elif message.frozen_by_inhibition:
            text += '\nInhibited'
        elif message.frozen_until:
            text += f'\nFrozen until {message.frozen_until_text or message.frozen_until}'
        return text

    @staticmethod
    def _actions(message: IncidentPresentation, message_id: str, *, freeze_menu=False):
        if message.status == 'closed':
            return []
        if freeze_menu and not message.frozen:
            labels = FREEZE_OPTIONS
        else:
            if message.frozen_by_maintenance:
                freeze = ('Maintenance', 'noop')
            elif message.frozen_by_inhibition:
                freeze = ('Inhibited', 'noop')
            elif message.can_unfreeze:
                freeze = (message.frozen_until_text or 'Unfreeze', 'unfreeze')
            elif message.frozen:
                freeze = (message.frozen_until_text or 'Frozen', 'noop')
            else:
                freeze = ('Freeze', 'freeze_menu')
            chain = 'Release' if not message.chain_enabled and message.status == 'resolved' else 'Take It'
            labels = [(chain, 'chain'), freeze]
            if message.can_create_task and not message.task_link:
                labels.append(('Create Task', 'task'))
        return [{'type': 'button', 'text': text, 'msg': f'{COMMAND_PREFIX} {message_id} {action}',
                 'msg_in_chat_window': True, 'msg_processing_type': 'sendMessage'} for text, action in labels]

    async def create_incident(self, message: IncidentPresentation) -> MessageRef | None:
        message_id = uuid4().hex
        response = await self.http.post(f'{self.url}/api/v1/chat.sendMessage', headers=self.headers, json={
            'message': {'_id': message_id, 'rid': message.channel_id, 'msg': self._content(message),
                        'attachments': [{'actions': self._actions(message, message_id),
                                         'button_alignment': 'horizontal'}]},
        })
        status = response.status
        if not 200 <= status < 300:
            response.close()
            logger.error('Incident message creation failed', extra={'messenger': 'rocketchat', 'status': status})
            return None
        data = await self._read(response)
        if data.get('success') and data.get('message', {}).get('_id') == message_id:
            return MessageRef(message.channel_id, message_id)
        logger.error('Incident message creation failed', extra={'messenger': 'rocketchat', 'status': status})
        return None

    async def update_incident(self, message: IncidentPresentation, *, freeze_menu=False) -> None:
        text = self._content(message)
        show_menu = freeze_menu and not message.frozen and message.status != 'closed'
        request_id = uuid4().hex
        actions = self._actions(message, message.thread_id, freeze_menu=show_menu)
        attachments = ([{'actions': actions[:3], 'button_alignment': 'horizontal'},
                        {'actions': actions[3:], 'button_alignment': 'horizontal'}]
                       if show_menu else [{'actions': actions, 'button_alignment': 'horizontal'}])
        # An explicit preview list forces native updateMessage to apply button-only edits.
        response = await self.http.post(f'{self.url}/api/v1/method.call/updateMessage', headers=self.headers, json={
            'message': json.dumps({'msg': 'method', 'method': 'updateMessage', 'id': request_id, 'params': [{
                '_id': message.thread_id, 'rid': message.channel_id, 'msg': text,
                'attachments': attachments,
            }, []]}),
        })
        status = response.status
        if not 200 <= status < 300:
            response.close()
            raise RuntimeError(f'Rocket.Chat incident update failed (HTTP {status}); '
                               'check native updateMessage support and bot editing permissions')
        try:
            data = await self._read(response)
            result = json.loads(data['message'])
            valid = (data.get('success') is True and isinstance(result, dict)
                     and result.get('msg') == 'result' and result.get('id') == request_id
                     and result.get('error') is None)
        except (ValueError, KeyError, TypeError):
            valid = False
        if not valid:
            raise RuntimeError('Rocket.Chat incident update failed')

    async def post_notification(self, message: MessageRef, content: NotificationContent) -> DeliveryResult:
        text = content.text if content.header is None else f'{content.header}\n{content.text}'
        response = await self.http.post(f'{self.url}/api/v1/chat.postMessage', headers=self.headers, json={
            'roomId': message.channel_id, 'tmid': message.thread_id, 'text': text,
        })
        status = response.status
        if not 200 <= status < 300:
            response.close()
            return DeliveryResult(status)
        data = await self._read(response)
        return DeliveryResult(status if data.get('success') else 502)

    async def parse_interaction(self, request: InteractionRequest) -> Interaction | ProviderResponse:
        if request.method != 'POST':
            return ProviderResponse(status_code=405)
        try:
            payload = json.loads(request.body)
            if not isinstance(payload, dict):
                raise ValueError('invalid payload')
            token = payload.get('token')
            if not isinstance(token, str) or not hmac.compare_digest(token.encode(), self._webhook_token.encode()):
                return ProviderResponse(status_code=401)
            if payload.get('bot') or payload.get('isEdited'):
                return ProviderResponse()
            timestamp = datetime.fromisoformat(payload['timestamp'].replace('Z', '+00:00'))
            if timestamp.tzinfo is None or abs((datetime.now(timezone.utc) - timestamp).total_seconds()) > CALLBACK_MAX_AGE_SECONDS:
                return ProviderResponse(status_code=401)
            channel_id, actor_id = payload['channel_id'], payload['user_id']
            if (not isinstance(channel_id, str) or channel_id not in self._channels
                    or not isinstance(actor_id, str) or not actor_id):
                raise ValueError('invalid channel or actor')
            prefix, message_id, action = payload['text'].split()
            if prefix != COMMAND_PREFIX or not message_id or action not in COMMANDS:
                raise ValueError('invalid command')
            command = COMMANDS[action]
            if command.action == InteractionAction.FREEZE:
                response = await self.http.get(f'{self.url}/api/v1/chat.getMessage', headers=self.headers,
                                               params={'msgId': message_id})
                if response.status != 200:
                    response.close()
                    return ProviderResponse(status_code=503)
                data = await self._read(response)
                original = data['message']
                if (data.get('success') is not True or original['_id'] != message_id or original['rid'] != channel_id
                        or original['u']['_id'] != self.headers['X-User-Id']):
                    return ProviderResponse(status_code=400)
                # Commands are visible: accept durations only while their menu is still rendered.
                if not any(button.get('msg') == f'{COMMAND_PREFIX} {message_id} {action}'
                           for attachment in original.get('attachments', [])
                           for button in attachment.get('actions', [])):
                    return ProviderResponse()
            return Interaction(MessageRef(channel_id, message_id), actor_id, (command,))
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, ValueError, TypeError, AttributeError, OverflowError):
            return ProviderResponse(status_code=400)

    async def after_interaction(self, message: IncidentPresentation, interaction: Interaction) -> None:
        freeze_menu = any(command.action == InteractionAction.SHOW_FREEZE_OPTIONS for command in interaction.commands)
        await self.update_incident(message, freeze_menu=freeze_menu)

    @staticmethod
    def respond_to_interaction(message: IncidentPresentation) -> ProviderResponse:
        return ProviderResponse()

    @staticmethod
    def incident_url(message: MessageRef, identity: ProviderIdentity) -> str:
        address = urlsplit(identity.public_url or '')
        return 'https://go.rocket.chat/room?' + urlencode({
            'host': address.netloc + address.path, 'rid': message.channel_id, 'mid': message.thread_id,
        })

    @staticmethod
    def user_url(user: UserProfile, identity: ProviderIdentity) -> None:
        return None

    @staticmethod
    def mention_id(user: UserProfile) -> str | None:
        return user.username

    @staticmethod
    def template_source(name: str) -> str:
        if name not in TEMPLATE_NAMES:
            raise KeyError(name)
        return files(__package__).joinpath('resources', name + '.j2').read_text(encoding='utf-8')
