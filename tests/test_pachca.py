from dataclasses import replace
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

from impulse_messenger_api import (
    AuthenticationError, Interaction, InteractionAction, InteractionRequest, MessageRef,
    NotificationContent, ProviderContext, ProviderIdentity, REQUIRED_TEMPLATE_NAMES,
)
from impulse_pachca import API_BASE, PachcaProvider
from impulse_pachca.authentication import PachcaAuthentication
from impulse_pachca.config import PachcaApplicationConfig, PachcaChannel, PachcaGroup, PachcaUser
from impulse_pachca.plugin import plugin


@pytest.fixture
def provider(http):
    config = PachcaApplicationConfig(
        impulse_address='https://impulse.example', admin_users=['Dmitry'],
        channels={'incidents': {'id': 334}}, users={'Dmitry': {'id': 12}}, groups={'devops': {'id': 9111}},
    )
    instance = PachcaProvider(config, {'PACHCA_BOT_TOKEN': 'token', 'PACHCA_SIGNING_SECRET': 'secret'})
    instance.http = http
    return instance


def signed_request(payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    signature = hmac.new(b'secret', body, hashlib.sha256).hexdigest()
    return InteractionRequest('POST', (('Pachca-Signature', signature),), (), body)


def button_payload(**changes):
    return dict(webhook_timestamp=1000, type='button', chat_id=334, message_id=194275,
                user_id=12, data='stop_chain') | changes


@pytest.mark.asyncio
async def test_initialize_activate_and_profiles(provider, http, response):
    identity = await provider.initialize(ProviderContext(http, 'https://impulse.example/app'))
    assert identity.public_url == 'https://app.pachca.com'
    registered = response()
    http.put.return_value = registered
    await provider.activate()
    http.put.assert_awaited_once_with(
        f'{API_BASE}/bot/webhook', headers=provider.headers,
        json={'webhook': {'outgoing_url': 'https://impulse.example/app'}},
    )
    registered.close.assert_called_once()
    user_reply = response({'data': {'first_name': 'Dmitry', 'last_name': None, 'nickname': 'dmitry',
                                    'email': None, 'time_zone': 'Europe/Moscow'}})
    http.get.return_value = user_reply
    user = await provider.fetch_user(12)
    assert (user.id, user.full_name, user.username, user.timezone) == (12, 'Dmitry', 'dmitry', 'Europe/Moscow')
    assert provider.mention_id(user) == 'dmitry'
    assert provider.user_url(user, identity) is None
    user_reply.close.assert_called_once()
    http.get.return_value = response({'data': {'name': 'DevOps'}})
    groups = await provider.fetch_groups()
    assert (groups[0].id, groups[0].name) == (9111, 'DevOps')


@pytest.mark.asyncio
async def test_message_creation_update_and_thread(provider, http, response, presentation):
    message = replace(presentation, channel_id=334, thread_id='194275', can_create_task=True)
    created = response({'data': {'id': 194275}}, status=201)
    http.post.return_value = created
    assert await provider.create_incident(message) == MessageRef(334, '194275')
    payload = http.post.call_args.kwargs['json']['message']
    assert payload['entity_id'] == 334
    assert payload['content'] == '🔥 Alert\nDetails'
    assert [button['data'] for button in payload['buttons'][0]] == ['stop_chain', 'freeze_menu', 'task']
    created.close.assert_called_once()
    updated = response(status=200)
    http.put.return_value = updated
    await provider.update_incident(message)
    assert http.put.call_args.args == (f'{API_BASE}/messages/194275',)
    assert http.put.call_args.kwargs['json']['message'] == {
        'content': payload['content'], 'buttons': payload['buttons'],
    }
    updated.close.assert_called_once()
    thread, notification = response({'data': {'id': 265142}}, status=201), response(status=201)
    http.post.side_effect = [thread, notification]
    result = await provider.post_notification(MessageRef(334, '194275'), NotificationContent('notify'))
    assert result.status_code == 201
    assert http.post.call_args_list[-2].args == (f'{API_BASE}/messages/194275/thread',)
    assert http.post.call_args.kwargs['json'] == {
        'message': {'entity_type': 'thread', 'entity_id': 265142, 'content': 'notify'},
    }
    thread.close.assert_called_once()
    notification.close.assert_called_once()


@pytest.mark.asyncio
async def test_write_failures_are_visible(provider, http, response, presentation):
    http.post.return_value = response(status=403)
    assert await provider.create_incident(presentation) is None
    result = await provider.post_notification(MessageRef(334, '194275'), NotificationContent('notify'))
    assert result.status_code == 403
    assert http.post.await_count == 2
    http.put.return_value = response(status=403)
    with pytest.raises(RuntimeError, match='HTTP 403'):
        await provider.update_incident(presentation)
    http.put.return_value.close.assert_called_once()


def test_button_states_and_resources(provider, presentation):
    assert provider._buttons(replace(presentation, status='closed')) == []
    assert provider._buttons(replace(presentation, status='resolved', chain_enabled=False))[0][0]['data'] == 'start_chain'
    assert provider._freeze_button(replace(presentation, frozen_by_maintenance=True))['data'] == 'noop'
    assert provider._freeze_button(replace(presentation, frozen_by_inhibition=True))['data'] == 'noop'
    assert provider._freeze_button(replace(presentation, can_unfreeze=True, frozen_until_text='tomorrow')) == {
        'text': 'tomorrow', 'data': 'unfreeze',
    }
    assert [row[0]['data'] for row in provider._buttons(presentation, freeze_menu=True)] == [
        'freeze_tomorrow', 'freeze_next_monday', 'freeze_month', 'freeze_6months', 'freeze_back',
    ]
    for name in REQUIRED_TEMPLATE_NAMES:
        assert plugin.template_source(name)
    with pytest.raises(KeyError):
        plugin.template_source('../body')
    assert plugin.incident_url(MessageRef(334, '194275'), ProviderIdentity('https://app.pachca.com')) == (
        'https://app.pachca.com/chats/334?message=194275'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(('data', 'action'), [
    ('stop_chain', InteractionAction.ASSIGN), ('start_chain', InteractionAction.RELEASE),
    ('freeze_menu', InteractionAction.SHOW_FREEZE_OPTIONS), ('freeze_tomorrow', InteractionAction.FREEZE),
    ('freeze_back', InteractionAction.NOOP), ('task', InteractionAction.CREATE_TASK),
])
async def test_verified_button_commands(provider, monkeypatch, data, action):
    monkeypatch.setattr('impulse_pachca.time.time', lambda: 1000)
    result = await provider.parse_interaction(signed_request(button_payload(data=data)))
    assert isinstance(result, Interaction)
    assert result.message == MessageRef(334, '194275')
    assert result.actor_id == 12
    assert result.commands[0].action == action


@pytest.mark.asyncio
@pytest.mark.parametrize(('payload', 'status'), [
    (button_payload(webhook_timestamp=939), 401), (button_payload(webhook_timestamp=1061), 401),
    (button_payload(webhook_timestamp=float('nan')), 401),
    (button_payload(webhook_timestamp=float('inf')), 401),
    (button_payload(webhook_timestamp=10 ** 400), 401),
    (button_payload(webhook_timestamp=True), 400), (button_payload(webhook_timestamp='1000'), 400),
    (button_payload(chat_id=None), 400), (button_payload(chat_id=0), 400),
    (button_payload(message_id=-1), 400), (button_payload(user_id=True), 400),
    (button_payload(user_id='12'), 400), (button_payload(data='unknown'), 400),
    (button_payload(data=[]), 400), ([], 400), (None, 400), (b'not JSON', 400), (b'\xff', 400),
])
async def test_reject_invalid_callback(provider, monkeypatch, payload, status):
    monkeypatch.setattr('impulse_pachca.time.time', lambda: 1000)
    assert (await provider.parse_interaction(signed_request(payload))).status_code == status


@pytest.mark.asyncio
async def test_signature_and_other_events(provider, monkeypatch):
    monkeypatch.setattr('impulse_pachca.time.time', lambda: 1000)
    request = signed_request(button_payload())
    assert (await provider.parse_interaction(replace(request, body=request.body + b' '))).status_code == 401
    assert (await provider.parse_interaction(replace(request, headers=()))).status_code == 401
    assert (await provider.parse_interaction(signed_request(button_payload(type='message')))).status_code == 200


@pytest.mark.asyncio
async def test_oauth_exchange(http, response):
    auth = PachcaAuthentication('client', 'secret')
    url = urlparse(auth.build_authorization_url('state', 'https://impulse.example/auth/callback'))
    assert url.scheme + '://' + url.netloc + url.path == 'https://app.pachca.com/apps/authorize'
    assert parse_qs(url.query) == {'response_type': ['code'], 'client_id': ['client'],
                                  'redirect_uri': ['https://impulse.example/auth/callback'], 'state': ['state']}
    token, info = response({'access_token': 'access'}), response({'data': {'user_id': 12}})
    http.post.return_value, http.get.return_value = token, info
    user = await auth.authenticate_callback({'code': 'code', 'iss': 'https://api.pachca.com'},
                                            'https://impulse.example/auth/callback', http)
    assert user.id == '12'
    assert http.post.call_args.args == (f'{API_BASE}/oauth/token',)
    assert http.post.call_args.kwargs['data']['client_secret'] == 'secret'
    http.get.assert_awaited_once_with(f'{API_BASE}/oauth/token/info', headers={'Authorization': 'Bearer access'})
    token.close.assert_called_once()
    info.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('params', [{'error': 'access_denied'}, {}, {'code': ' '}, {'code': 'code', 'iss': 'wrong'}])
async def test_oauth_reject_invalid_callback(http, params):
    with pytest.raises(AuthenticationError):
        await PachcaAuthentication('client', 'secret').authenticate_callback(params, 'https://impulse.example/callback', http)
    http.post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(('token_data', 'info_data', 'status'), [
    ([], None, 200), ({'access_token': 12}, None, 200), ({}, None, 200),
    ({'access_token': 'access'}, {}, 200), ({'access_token': 'access'}, {'data': None}, 200),
    ({'access_token': 'access'}, {'data': {'user_id': None}}, 200),
    ({'access_token': 'access'}, {'data': {'user_id': True}}, 200),
    ({'access_token': 'access'}, {'data': {'user_id': 0}}, 200),
    ({'access_token': 'access'}, {'data': {'user_id': -1}}, 200),
    ({'access_token': 'access'}, {'data': {'user_id': '12'}}, 200),
    ({}, None, 401),
])
async def test_oauth_reject_invalid_response(http, response, token_data, info_data, status):
    token, info = response(token_data, status), response(info_data)
    http.post.return_value, http.get.return_value = token, info
    with pytest.raises(AuthenticationError):
        await PachcaAuthentication('client', 'secret').authenticate_callback({'code': 'code'}, 'https://impulse.example/callback', http)
    token.close.assert_called_once()
    if http.get.await_count:
        info.close.assert_called_once()


@pytest.mark.parametrize('model', [PachcaUser, PachcaChannel, PachcaGroup])
@pytest.mark.parametrize('user_id', [0, -1])
def test_config_rejects_nonpositive_ids(model, user_id):
    with pytest.raises(ValidationError):
        model(id=user_id)
