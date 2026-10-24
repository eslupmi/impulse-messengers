"""Teams Bot Connector REST protocol, verified callbacks and templates."""
import json
import logging
import re
import time
from importlib.resources import files
from urllib.parse import quote, urlencode, urlsplit

import jwt

from impulse_messenger_api import (
    DeliveryResult, IncidentPresentation, Interaction, InteractionAction, InteractionCommand, InteractionRequest,
    MessageRef, NotificationContent, ProviderContext, ProviderDescriptor, ProviderIdentity, ProviderResponse,
    REQUIRED_TEMPLATE_NAMES, SecretResolver, UserProfile,
)

from .config import GUID_PATTERN, TeamsApplicationConfig

logger = logging.getLogger('main_logger')
OPENID_URL = 'https://login.botframework.com/v1/.well-known/openidconfiguration'
TOKEN_ISSUER = 'https://api.botframework.com'
TEMPLATE_NAMES = frozenset(REQUIRED_TEMPLATE_NAMES)
MENTION = re.compile(r'<at>([0-9a-f]{8}-[0-9a-f-]{27})</at>')
COMMANDS = {
    'assign': InteractionCommand(InteractionAction.ASSIGN),
    'release': InteractionCommand(InteractionAction.RELEASE),
    'task': InteractionCommand(InteractionAction.CREATE_TASK),
    'unfreeze': InteractionCommand(InteractionAction.UNFREEZE),
    'noop': InteractionCommand(InteractionAction.NOOP),
    'freeze_tomorrow': InteractionCommand(InteractionAction.FREEZE, 'tomorrow'),
    'freeze_next_monday': InteractionCommand(InteractionAction.FREEZE, 'next_monday'),
    'freeze_month': InteractionCommand(InteractionAction.FREEZE, 'month'),
    'freeze_6months': InteractionCommand(InteractionAction.FREEZE, '6months'),
}


def _thread_id(conversation_id, activity_id):
    # Connector conversation IDs are opaque; keep both returned IDs in core's existing string field.
    return json.dumps([conversation_id, activity_id], separators=(',', ':'))


def _thread_parts(thread_id):
    data = json.loads(thread_id)
    if not isinstance(data, list) or len(data) != 2 or not all(isinstance(item, str) and item for item in data):
        raise ValueError('Invalid Teams thread reference')
    return data


class TeamsProvider:
    descriptor = ProviderDescriptor('teams', rate_limit=3, notification_headers=False)
    config_model = TeamsApplicationConfig

    def __init__(self, config: TeamsApplicationConfig, secrets: SecretResolver):
        self._app_id = secrets.get('TEAMS_APP_ID')
        self._app_secret = secrets.get('TEAMS_APP_SECRET')
        if not self._app_id or not self._app_secret:
            raise ValueError('TEAMS_APP_ID and TEAMS_APP_SECRET are required')
        self.url = config.service_url.rstrip('/')
        self.team = config.team_id
        self._tenant_id = config.tenant_id
        self._channels = {channel.id for channel in config.channels.values()}
        self._profiles = {}
        self._token = None
        self._token_expires = 0
        self._keys = []
        self._algorithms = []
        self._keys_updated = 0

    async def initialize(self, context: ProviderContext) -> ProviderIdentity:
        self.http = context.http
        return ProviderIdentity('https://teams.microsoft.com', self._tenant_id)

    async def activate(self) -> None:
        await self._headers()
        await self._refresh_keys()

    @staticmethod
    async def _read(response):
        try:
            if not 200 <= response.status < 300:
                raise RuntimeError(f'Teams request failed (HTTP {response.status})')
            return await response.json()
        finally:
            response.close()

    async def _headers(self):
        if time.monotonic() >= self._token_expires:
            data = await self._read(await self.http.post(
                f'https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token',
                data={'grant_type': 'client_credentials', 'client_id': self._app_id,
                      'client_secret': self._app_secret, 'scope': TOKEN_ISSUER + '/.default'},
                allow_redirects=False,
            ))
            if not isinstance(data.get('access_token'), str) or not data['access_token']:
                raise RuntimeError('Teams access token not found in response')
            self._token = data['access_token']
            self._token_expires = time.monotonic() + max(0, int(data['expires_in']) - 60)
        return {'Authorization': f'Bearer {self._token}'}

    async def _refresh_keys(self):
        metadata = await self._read(await self.http.get(OPENID_URL, allow_redirects=False))
        key_url = urlsplit(metadata['jwks_uri'])
        if key_url.scheme != 'https' or key_url.netloc != 'login.botframework.com' or key_url.fragment:
            raise ValueError('Invalid Bot Framework signing-key URL')
        algorithms = set(metadata['id_token_signing_alg_values_supported']) & {'RS256', 'RS384', 'RS512'}
        if not algorithms:
            raise ValueError('No supported Bot Framework signing algorithms')
        data = await self._read(await self.http.get(metadata['jwks_uri'], allow_redirects=False))
        self._keys = data['keys']
        self._algorithms = list(algorithms)
        self._keys_updated = time.monotonic()

    async def _verify_token(self, token, service_url):
        header = jwt.get_unverified_header(token)
        if header.get('alg') not in {'RS256', 'RS384', 'RS512'}:
            raise ValueError('Invalid Bot Framework signing algorithm')
        now = time.monotonic()
        if not self._keys or now - self._keys_updated >= 86400:
            await self._refresh_keys()
        key = next((key for key in self._keys if key.get('kid') == header.get('kid')), None)
        if key is None and now - self._keys_updated >= 300:
            await self._refresh_keys()
            key = next((key for key in self._keys if key.get('kid') == header.get('kid')), None)
        if not header.get('kid') or key is None or 'msteams' not in key.get('endorsements', ()):
            raise ValueError('Signing key does not endorse Teams')
        claims = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=self._algorithms,
                            audience=self._app_id, issuer=TOKEN_ISSUER, leeway=300,
                            options={'require': ['iss', 'aud', 'exp', 'nbf', 'serviceurl']})
        if claims['serviceurl'] != service_url:
            raise ValueError('Bot Framework service URL does not match activity')

    async def fetch_user(self, user_id: str | int) -> UserProfile:
        # shortcut: Scan the roster per user refresh; cache a roster snapshot if large-team lookups become costly.
        headers = await self._headers()
        params = {'pageSize': 500}
        while True:
            response = await self.http.get(f'{self.url}/v3/conversations/{quote(self.team, safe="")}/pagedmembers',
                                           headers=headers, params=params, allow_redirects=False)
            if response.status != 200:
                status = response.status
                response.close()
                logger.warning('Teams user lookup failed', extra={'user_id': user_id, 'status': status})
                return UserProfile(id=user_id, exists=False)
            data = await self._read(response)
            for member in data['members']:
                if member.get('aadObjectId') == str(user_id):
                    profile = UserProfile(id=user_id, exists=True, full_name=member.get('name'),
                                          username=member.get('userPrincipalName'), email=member.get('email'))
                    self._profiles[str(user_id)] = profile
                    return profile
            if not data.get('continuationToken'):
                self._profiles.pop(str(user_id), None)
                return UserProfile(id=user_id, exists=False)
            params = {'pageSize': 500, 'continuationToken': data['continuationToken']}

    async def fetch_groups(self):
        return ()

    @staticmethod
    def _action(title, command):
        return {'type': 'Action.Submit', 'title': title, 'data': {'command': command}}

    @classmethod
    def _actions(cls, message):
        if message.status == 'closed':
            return []
        release = not message.chain_enabled and message.status == 'resolved'
        actions = [cls._action('Release' if release else 'Take It', 'release' if release else 'assign')]
        if message.frozen_by_maintenance:
            actions.append(cls._action('Maintenance', 'noop'))
        elif message.frozen_by_inhibition:
            actions.append(cls._action('Inhibited', 'noop'))
        elif message.can_unfreeze:
            actions.append(cls._action(message.frozen_until_text or 'Unfreeze', 'unfreeze'))
        elif message.frozen_until:
            actions.append(cls._action(message.frozen_until_text or 'Frozen', 'noop'))
        else:
            actions.append({'type': 'Action.ShowCard', 'title': 'Freeze', 'card': {
                'type': 'AdaptiveCard', 'body': [], 'actions': [
                    cls._action(title, command) for title, command in (
                        ('Tomorrow', 'freeze_tomorrow'), ('Next Monday', 'freeze_next_monday'),
                        ('In Month', 'freeze_month'), ('In 6 months', 'freeze_6months'),
                    )
                ],
            }})
        if message.can_create_task and not message.task_link:
            actions.append(cls._action('📌', 'task'))
        return actions

    def _activity(self, text, actions=()):
        entities = []

        def mention(match):
            user_id = match[1]
            profile = self._profiles.get(user_id)
            name = (profile.full_name or profile.username or user_id) if profile else user_id
            rendered = f'<at>{name}</at>'
            entity = {'type': 'mention', 'text': rendered, 'mentioned': {'id': user_id, 'name': name}}
            if entity not in entities:
                entities.append(entity)
            return rendered

        text = MENTION.sub(mention, text)
        card = {'type': 'AdaptiveCard', '$schema': 'https://adaptivecards.io/schemas/adaptive-card.json',
                'version': '1.2', 'body': [{'type': 'TextBlock', 'text': text, 'wrap': True}],
                'actions': list(actions), 'msteams': {'entities': entities}}
        return {'type': 'message', 'summary': 'IMPulse incident update',
                'attachments': [{'contentType': 'application/vnd.microsoft.card.adaptive', 'content': card}]}

    def _incident_activity(self, message):
        return self._activity(f'{message.status_icon} {message.header}\n\n{message.body}', self._actions(message))

    async def create_incident(self, message: IncidentPresentation) -> MessageRef | None:
        response = await self.http.post(f'{self.url}/v3/conversations', headers=await self._headers(), json={
            'isGroup': True, 'tenantId': self._tenant_id,
            'channelData': {'channel': {'id': message.channel_id}, 'tenant': {'id': self._tenant_id}},
            'activity': self._incident_activity(message),
        }, allow_redirects=False)
        if not 200 <= response.status < 300:
            status = response.status
            response.close()
            logger.error('Teams incident creation failed', extra={'status': status})
            return None
        data = await self._read(response)
        if not all(isinstance(data.get(key), str) and data[key] for key in ('id', 'activityId')):
            raise ValueError('Teams did not return conversation and activity IDs')
        return MessageRef(message.channel_id, _thread_id(data['id'], data['activityId']))

    async def update_incident(self, message: IncidentPresentation) -> None:
        conversation_id, activity_id = _thread_parts(message.thread_id)
        activity = self._incident_activity(message)
        activity['id'] = activity_id
        await self._read(await self.http.put(
            f'{self.url}/v3/conversations/{quote(conversation_id, safe="")}/activities/{quote(activity_id, safe="")}',
            headers=await self._headers(), json=activity, allow_redirects=False,
        ))

    async def post_notification(self, message: MessageRef, content: NotificationContent) -> DeliveryResult:
        conversation_id, activity_id = _thread_parts(message.thread_id)
        activity = self._activity(content.text)
        activity['replyToId'] = activity_id
        response = await self.http.post(
            f'{self.url}/v3/conversations/{quote(conversation_id, safe="")}/activities/{quote(activity_id, safe="")}',
            headers=await self._headers(), json=activity, allow_redirects=False,
        )
        status = response.status
        response.close()
        return DeliveryResult(status)

    async def parse_interaction(self, request: InteractionRequest) -> Interaction | ProviderResponse:
        if request.method != 'POST':
            return ProviderResponse(status_code=405)
        headers = {key.lower(): value for key, value in request.headers}
        authorization = headers.get('authorization', '').split()
        if len(authorization) != 2 or authorization[0].lower() != 'bearer':
            return ProviderResponse(status_code=401)
        try:
            payload = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return ProviderResponse(status_code=400)
        if not isinstance(payload, dict):
            return ProviderResponse(status_code=400)
        try:
            await self._verify_token(authorization[1], payload['serviceUrl'])
            channel_data = payload['channelData']
            if (payload['channelId'] != 'msteams' or payload['serviceUrl'].rstrip('/') != self.url
                    or channel_data['tenant']['id'] != self._tenant_id):
                return ProviderResponse(status_code=403)
            if payload.get('type') != 'message' or not payload.get('value'):
                return ProviderResponse()
            if (channel_data['channel']['id'] not in self._channels
                    or channel_data['team']['id'] != self.team):
                return ProviderResponse(status_code=403)
            value = payload['value']
            if 'command' not in value:
                return ProviderResponse()
            conversation_id, activity_id = payload['conversation']['id'], payload['replyToId']
            actor_id = payload['from']['aadObjectId']
            if (not all(isinstance(item, str) and item for item in (conversation_id, activity_id, actor_id))
                    or not re.fullmatch(GUID_PATTERN, actor_id)):
                return ProviderResponse(status_code=400)
            return Interaction(MessageRef(channel_data['channel']['id'], _thread_id(conversation_id, activity_id)),
                               actor_id, (COMMANDS[value['command']],))
        except (jwt.PyJWTError, ValueError):
            return ProviderResponse(status_code=401)
        except (KeyError, TypeError, AttributeError):
            return ProviderResponse(status_code=400)

    async def after_interaction(self, message: IncidentPresentation, interaction: Interaction) -> None:
        await self.update_incident(message)

    @staticmethod
    def respond_to_interaction(message: IncidentPresentation) -> ProviderResponse:
        return ProviderResponse()

    @staticmethod
    def incident_url(message: MessageRef, identity: ProviderIdentity) -> str:
        _, activity_id = _thread_parts(message.thread_id)
        params = urlencode({'tenantId': identity.team or '', 'parentMessageId': activity_id})
        return f'{identity.public_url}/l/message/{quote(str(message.channel_id), safe="")}/{quote(activity_id, safe="")}?{params}'

    @staticmethod
    def user_url(user: UserProfile, identity: ProviderIdentity) -> str | None:
        return None

    @staticmethod
    def mention_id(user: UserProfile) -> str | int | None:
        return user.id

    @staticmethod
    def template_source(name: str) -> str:
        if name not in TEMPLATE_NAMES:
            raise KeyError(name)
        return files(__package__).joinpath('resources').joinpath(name + '.j2').read_text(encoding='utf-8')
