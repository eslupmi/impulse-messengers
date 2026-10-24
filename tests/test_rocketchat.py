from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from urllib.parse import parse_qs, urlsplit

from jinja2 import Environment
import pytest
from pydantic import ValidationError

from impulse_messenger_api import (
    AuthenticationError, Interaction, InteractionAction, InteractionRequest, InteractiveProvider,
    MessageRef, NotificationContent, ProviderContext, ProviderIdentity, REQUIRED_TEMPLATE_NAMES,
)
from impulse_rocketchat import RocketChatProvider
from impulse_rocketchat.authentication import RocketChatAuthentication
from impulse_rocketchat.config import RocketChatApplicationConfig
from impulse_rocketchat.plugin import plugin


@pytest.fixture
def config():
    return RocketChatApplicationConfig(
        address='https://chat.example/', impulse_address='https://impulse.example', admin_users=['Dmitry'],
        channels={'incidents': {'id': 'room'}}, users={'Dmitry': {'id': 'user'}}, groups={'devops': {'id': 'team'}},
    )


@pytest.fixture
def secrets():
    return {'ROCKETCHAT_AUTH_TOKEN': 'token', 'ROCKETCHAT_USER_ID': 'bot', 'ROCKETCHAT_WEBHOOK_TOKEN': 'secret'}


@pytest.fixture
def provider(config, secrets, http):
    instance = RocketChatProvider(config, secrets)
    instance.http = http
    return instance


def request(**changes):
    payload = dict(token='secret', channel_id='room', user_id='user', message_id='action-message',
                   timestamp=datetime.now(timezone.utc).isoformat(), bot=False, text='impulse parent chain') | changes
    return InteractionRequest('POST', (), (), json.dumps(payload).encode())


@pytest.mark.asyncio
async def test_lifecycle_profiles_groups_and_links(provider, http, response):
    assert isinstance(provider, InteractiveProvider)
    assert plugin.factory is RocketChatProvider
    assert plugin.config_model is RocketChatApplicationConfig
    identity = await provider.initialize(ProviderContext(http, 'https://impulse.example/app'))
    assert identity == ProviderIdentity('https://chat.example')
    await provider.activate()
    http.post.assert_not_awaited()
    reply = response({'success': True, 'user': {'name': 'Dmitry', 'username': 'dmitry',
                                              'emails': [{'address': 'd@example.org'}], 'utcOffset': 3}})
    http.get.return_value = reply
    user = await provider.fetch_user('user')
    http.get.assert_awaited_with('https://chat.example/api/v1/users.info', headers=provider.headers,
                                 params={'userId': 'user'})
    assert (user.id, user.exists, user.full_name, user.username, user.email) == (
        'user', True, 'Dmitry', 'dmitry', 'd@example.org')
    assert user.timezone is None
    assert provider.mention_id(user) == 'dmitry'
    assert provider.user_url(user, identity) is None
    reply.close.assert_called_once()
    http.get.return_value = response({'success': True, 'teamInfo': {'name': 'devops'}})
    groups = await provider.fetch_groups()
    assert (groups[0].id, groups[0].name, groups[0].exists) == ('team', 'devops', True)
    http.get.assert_awaited_with('https://chat.example/api/v1/teams.info', headers=provider.headers,
                                 params={'teamId': 'team'})
    link = urlsplit(provider.incident_url(MessageRef('room', 'parent'), identity))
    assert (link.scheme, link.netloc, link.path) == ('https', 'go.rocket.chat', '/room')
    assert parse_qs(link.query) == {'host': ['chat.example'], 'rid': ['room'], 'mid': ['parent']}


@pytest.mark.asyncio
async def test_missing_profiles_close_responses(provider, http, response):
    reply = response(status=404)
    http.get.return_value = reply
    assert not (await provider.fetch_user('missing')).exists
    reply.close.assert_called_once()
    reply = response({'success': False})
    http.get.return_value = reply
    assert not (await provider.fetch_user('missing')).exists
    reply.close.assert_called_once()
    reply = response(status=403)
    http.get.return_value = reply
    groups = await provider.fetch_groups()
    assert not groups[0].exists
    reply.close.assert_called_once()


@pytest.mark.asyncio
async def test_create_update_and_thread_payloads(provider, http, response, presentation, monkeypatch):
    monkeypatch.setattr('impulse_rocketchat.uuid4', lambda: type('Id', (), {'hex': 'parent'})())
    reply = response({'success': True, 'message': {'_id': 'parent'}})
    http.post.return_value = reply
    message = replace(presentation, can_create_task=True)
    assert await provider.create_incident(message) == MessageRef('room', 'parent')
    payload = http.post.call_args.kwargs['json']['message']
    assert (payload['_id'], payload['rid'], payload['msg']) == ('parent', 'room', '🔥 Alert\nDetails')
    actions = payload['attachments'][0]['actions']
    assert len(actions) == 4
    assert actions[0] == {'type': 'button', 'text': 'Take It / Release', 'msg': 'impulse parent chain',
                          'msg_in_chat_window': True, 'msg_processing_type': 'sendMessage'}
    assert actions[2]['msg'] == 'impulse parent task'
    assert actions[-1] == {'type': 'button', 'text': 'Freeze in IMPulse', 'url': 'https://impulse.example'}
    reply.close.assert_called_once()
    reply = response({'success': True})
    http.post.return_value = reply
    await provider.update_incident(message)
    assert http.post.call_args.args == ('https://chat.example/api/v1/chat.update',)
    assert http.post.call_args.kwargs['json'] == {
        'roomId': 'room', 'msgId': 'message', 'text': payload['msg'],
    }
    reply.close.assert_called_once()
    reply = response({'success': True}, status=201)
    http.post.return_value = reply
    assert (await provider.post_notification(MessageRef('room', 'parent'), NotificationContent('notify', 'Alert'))).status_code == 201
    assert http.post.call_args.kwargs['json'] == {'roomId': 'room', 'tmid': 'parent', 'text': 'Alert\nnotify'}
    reply.close.assert_called_once()


def test_closed_and_frozen_presentations(provider, presentation):
    assert provider._actions(replace(presentation, status='closed'), 'parent') == []
    assert len(provider._actions(presentation, 'parent')) == 3
    assert len(provider._actions(replace(presentation, can_create_task=True, task_link='https://task'), 'parent')) == 3
    frozen = replace(presentation, frozen=True, frozen_until='2026-10-25T10:00:00Z', frozen_until_text='Tomorrow')
    assert 'Frozen until Tomorrow' in provider._content(frozen)
    assert provider._actions(frozen, 'parent')[1]['msg'] == 'impulse parent unfreeze'
    assert 'Inhibited' in provider._content(replace(frozen, frozen_by_inhibition=True))
    assert 'Maintenance' in provider._content(replace(frozen, frozen_by_maintenance=True))


@pytest.mark.asyncio
@pytest.mark.parametrize('status,data', [(403, {}), (200, {'success': False}), (200, {'success': True, 'message': {}})])
async def test_write_failures(provider, http, response, presentation, status, data):
    reply = response(data, status)
    http.post.return_value = reply
    assert await provider.create_incident(presentation) is None
    reply.close.assert_called_once()
    reply = response({'success': False}, status)
    http.post.return_value = reply
    with pytest.raises(RuntimeError, match='incident update failed'):
        await provider.update_incident(presentation)
    reply.close.assert_called_once()
    reply = response({'success': False}, status)
    http.post.return_value = reply
    assert (await provider.post_notification(MessageRef('room', 'parent'), NotificationContent('notify'))).status_code == (
        status if status == 403 else 502)
    reply.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('action,expected,option', [
    ('chain', InteractionAction.TOGGLE_ASSIGNMENT, None), ('task', InteractionAction.CREATE_TASK, None),
    ('unfreeze', InteractionAction.UNFREEZE, None),
])
async def test_native_outgoing_callbacks(provider, action, expected, option):
    result = await provider.parse_interaction(request(text=f'impulse parent {action}'))
    assert isinstance(result, Interaction)
    assert result.message == MessageRef('room', 'parent')
    assert result.actor_id == 'user'
    assert (result.commands[0].action, result.commands[0].freeze_option) == (expected, option)


@pytest.mark.asyncio
@pytest.mark.parametrize('changes,status', [
    ({'token': 'wrong'}, 401), ({'token': None}, 401), ({'token': []}, 401),
    ({'channel_id': 'other-room'}, 400), ({'user_id': None}, 400), ({'user_id': 4}, 400),
    ({'text': 'impulse parent unknown'}, 400), ({'text': 'impulse parent chain trailing'}, 400),
    ({'text': 'impulse parent freeze_tomorrow'}, 400), ({'text': 'impulse parent freeze_next_monday'}, 400),
    ({'text': 'impulse parent freeze_month'}, 400), ({'text': 'impulse parent freeze_6months'}, 400),
    ({'text': 'impulse parent freeze_menu'}, 400),
    ({'text': 'wrong parent chain'}, 400), ({'text': []}, 400),
    ({'timestamp': 'invalid'}, 400), ({'timestamp': '2026-10-24T10:00:00'}, 401),
    ({'timestamp': (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()}, 401),
    ({'timestamp': (datetime.now(timezone.utc) + timedelta(minutes=6)).isoformat()}, 401),
    ({'bot': True}, 200), ({'isEdited': True}, 200),
])
async def test_callback_boundary(provider, changes, status):
    result = await provider.parse_interaction(request(**changes))
    assert result.status_code == status


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [b'[]', b'null', b'{', b'\xff'])
async def test_invalid_callback_bodies(provider, body):
    assert (await provider.parse_interaction(InteractionRequest('POST', (), (), body))).status_code == 400


@pytest.mark.asyncio
async def test_callback_method_and_refresh(provider, http, response, presentation):
    assert (await provider.parse_interaction(replace(request(), method='GET'))).status_code == 405
    http.post.return_value = response({'success': True})
    interaction = await provider.parse_interaction(request())
    await provider.after_interaction(presentation, interaction)
    assert http.post.call_args.args == ('https://chat.example/api/v1/chat.update',)
    assert provider.respond_to_interaction(presentation).body == b'{}'


def test_templates_render_and_use_native_mentions(provider):
    env = Environment()
    for name in REQUIRED_TEMPLATE_NAMES:
        env.parse(provider.template_source(name))
    with pytest.raises(KeyError):
        provider.template_source('../__init__')
    user = type('User', (), {'username': 'dmitry', 'exists': True})()
    group = type('Group', (), {'name': 'devops', 'exists': True})()
    assert '@dmitry' in env.from_string(provider.template_source('chain_step_user')).render(
        users={'Dmitry': user}, step={'value': 'Dmitry'})
    assert '@devops' in env.from_string(provider.template_source('chain_step_group')).render(
        groups={'devops': group}, step={'value': 'devops'})


def test_configuration_and_secrets(config, secrets):
    with pytest.raises(ValidationError, match='impulse_address'):
        RocketChatApplicationConfig(**(config.model_dump() | {'impulse_address': None}))
    for name in secrets:
        with pytest.raises(ValueError, match=name):
            RocketChatProvider(config, secrets | {name: ''})


@pytest.mark.asyncio
async def test_oauth_code_exchange_and_identity(http, response, config):
    auth = plugin.authentication_factory('client', 'secret', config)
    assert isinstance(auth, RocketChatAuthentication)
    url = urlsplit(auth.build_authorization_url('state', 'https://impulse.example/auth'))
    assert url.path == '/oauth/authorize'
    assert parse_qs(url.query) == {'response_type': ['code'], 'client_id': ['client'],
                                  'redirect_uri': ['https://impulse.example/auth'], 'state': ['state']}
    token = response({'access_token': 'access'})
    user = response({'_id': 'user', 'username': 'dmitry', 'name': 'Dmitry', 'email': 'd@example.org'})
    http.post.return_value, http.get.return_value = token, user
    authenticated = await auth.authenticate_callback({'code': 'code'}, 'https://impulse.example/auth', http)
    assert (authenticated.id, authenticated.username, authenticated.full_name) == ('user', 'dmitry', 'Dmitry')
    assert http.post.call_args.args == ('https://chat.example/oauth/token',)
    assert http.post.call_args.kwargs['data']['grant_type'] == 'authorization_code'
    http.get.assert_awaited_once_with('https://chat.example/api/v1/me', headers={'Authorization': 'Bearer access'})
    token.close.assert_called_once()
    user.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('params,error', [({}, 'missing_code'), ({'error': 'access_denied'}, 'provider_error')])
async def test_oauth_missing_code_and_provider_error(http, params, error):
    with pytest.raises(AuthenticationError) as exception:
        await RocketChatAuthentication('https://chat.example', 'client', 'secret').authenticate_callback(params, '', http)
    assert exception.value.code == error
    http.post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('data,status', [({}, 200), ([], 200), ({'error': 'denied'}, 403)])
async def test_oauth_failed_token_responses(http, response, data, status):
    reply = response(data, status)
    http.post.return_value = reply
    with pytest.raises(AuthenticationError):
        await RocketChatAuthentication('https://chat.example', 'client', 'secret').authenticate_callback({'code': 'code'}, '', http)
    reply.close.assert_called_once()
