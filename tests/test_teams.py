import json
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock

import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa
from jinja2 import Environment
from pydantic import ValidationError

from impulse_messenger_api import (
    AuthenticationError, Interaction, InteractionAction, InteractionRequest, InteractiveProvider,
    MessageRef, NotificationContent, ProviderContext, ProviderIdentity, ProviderResponse, REQUIRED_TEMPLATE_NAMES,
    UserProfile,
)
from impulse_teams import OPENID_URL, TeamsProvider
from impulse_teams.authentication import TeamsAuthentication
from impulse_teams.config import TeamsApplicationConfig
from impulse_teams.plugin import plugin

TENANT = '11111111-1111-1111-1111-111111111111'
USER = '22222222-2222-2222-2222-222222222222'
APP = '33333333-3333-3333-3333-333333333333'
SERVICE = 'https://smba.trafficmanager.net/emea/'
THREAD = '["opaque/conversation;not-derived-from-room","root/activity"]'


@pytest.fixture
def config():
    return TeamsApplicationConfig(tenant_id=TENANT, team_id='team', service_url=SERVICE,
                                  impulse_address='https://impulse.test', admin_users=['Alice'],
                                  channels={'incidents': {'id': 'room'}}, users={'Alice': {'id': USER}})


@pytest.fixture(scope='module')
def signing_key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    public.update(kid='test-key', endorsements=['msteams'])
    return private, public


@pytest_asyncio.fixture
async def provider(config, http, signing_key):
    provider = TeamsProvider(config, {'TEAMS_APP_ID': APP, 'TEAMS_APP_SECRET': 'secret'})
    await provider.initialize(ProviderContext(http, 'https://impulse.test/app'))
    provider._token = 'cached-token'
    provider._token_expires = time.monotonic() + 3600
    provider._keys = [dict(signing_key[1])]
    provider._algorithms = ['RS256']
    provider._keys_updated = time.monotonic()
    return provider


@pytest.fixture
def payload():
    return {
        'type': 'message', 'channelId': 'msteams', 'serviceUrl': SERVICE,
        'channelData': {'tenant': {'id': TENANT}, 'team': {'id': 'team'}, 'channel': {'id': 'room'}},
        'conversation': {'id': 'opaque/conversation;not-derived-from-room'}, 'replyToId': 'root/activity',
        'from': {'id': '29:bot-specific-user', 'aadObjectId': USER}, 'value': {'command': 'assign'},
    }


def request(payload, signing_key, **overrides):
    now = int(time.time())
    claims = {'iss': 'https://api.botframework.com', 'aud': APP,
              'exp': now + 600, 'nbf': now - 1, 'serviceurl': payload['serviceUrl']}
    claims.update(overrides)
    token = jwt.encode(claims, signing_key[0], algorithm='RS256', headers={'kid': 'test-key'})
    return InteractionRequest('POST', (('Authorization', f'Bearer {token}'),), (), json.dumps(payload).encode())


def test_configuration_and_registration(config):
    assert config.service_url == SERVICE.rstrip('/')
    assert plugin.config_model is TeamsApplicationConfig
    assert plugin.factory is TeamsProvider
    auth = plugin.authentication_factory('client', 'secret', config)
    assert TENANT in auth.authority
    assert TeamsProvider.descriptor.api_version == 1


@pytest.mark.parametrize('field,value', [
    ('impulse_address', None), ('groups', {'tag': {'id': 'tag-id'}}),
    ('service_url', 'http://smba.trafficmanager.net/emea'),
    ('service_url', 'https://smba.trafficmanager.net.evil.test/emea'),
    ('service_url', 'https://smba.trafficmanager.net@evil.test/emea'),
    ('service_url', 'https://smba.trafficmanager.net/emea?redirect=evil'),
    ('tenant_id', '../other-tenant'), ('users', {'Alice': {'id': '29:bot-specific-user'}}),
])
def test_unsupported_configuration_is_rejected(config, field, value):
    with pytest.raises(ValidationError):
        TeamsApplicationConfig(**{**config.model_dump(), field: value})


@pytest.mark.parametrize('secrets', [{}, {'TEAMS_APP_ID': APP}, {'TEAMS_APP_SECRET': 'secret'}])
def test_required_credentials(config, secrets):
    with pytest.raises(ValueError, match='TEAMS_APP_ID and TEAMS_APP_SECRET'):
        TeamsProvider(config, secrets)


@pytest.mark.asyncio
async def test_real_contract_and_identity(provider):
    assert isinstance(provider, InteractiveProvider)
    assert provider.team == 'team'
    assert await provider.fetch_groups() == ()
    assert provider.mention_id(UserProfile(USER, True)) == USER
    assert provider.user_url(UserProfile(USER, True), ProviderIdentity()) is None


@pytest.mark.asyncio
async def test_create_update_reply_preserve_opaque_references(provider, presentation, http, response):
    created = response({'id': 'opaque/conversation;not-derived-from-room', 'activityId': 'root/activity'}, 201)
    updated, replied = response({'id': 'root/activity'}), response({'id': 'reply'}, 201)
    http.post.side_effect = [created, replied]
    http.put.return_value = updated
    ref = await provider.create_incident(presentation)
    assert ref == MessageRef('room', THREAD)
    created_payload = http.post.call_args.kwargs['json']
    assert created_payload['channelData']['channel']['id'] == 'room'
    assert created_payload['isGroup'] is True
    assert created_payload['activity']['attachments'][0]['content']['actions'][0]['data'] == {'command': 'assign'}
    await provider.update_incident(replace(presentation, thread_id=ref.thread_id))
    url = http.put.call_args.args[0]
    assert '/opaque%2Fconversation%3Bnot-derived-from-room/activities/root%2Factivity' in url
    assert http.put.call_args.kwargs['json']['id'] == 'root/activity'
    result = await provider.post_notification(ref, NotificationContent('Changed', 'unused header'))
    assert result.status_code == 201
    assert http.post.call_args.args[0] == url
    assert http.post.call_args.kwargs['json']['replyToId'] == 'root/activity'
    for item in (created, updated, replied):
        item.close.assert_called_once()
    link = provider.incident_url(ref, ProviderIdentity('https://teams.microsoft.com', TENANT))
    assert '/room/root%2Factivity?' in link
    assert parse_qs(urlsplit(link).query) == {'tenantId': [TENANT], 'parentMessageId': ['root/activity']}


@pytest.mark.asyncio
async def test_delivery_failures_close_responses(provider, presentation, http, response):
    created, replied, updated = response(status=403), response(status=429), response(status=500)
    http.post.side_effect = [created, replied]
    http.put.return_value = updated
    assert await provider.create_incident(presentation) is None
    assert (await provider.post_notification(MessageRef('room', THREAD), NotificationContent('test'))).status_code == 429
    with pytest.raises(RuntimeError, match='HTTP 500'):
        await provider.update_incident(replace(presentation, thread_id=THREAD))
    for item in (created, replied, updated):
        item.close.assert_called_once()


@pytest.mark.asyncio
async def test_paginated_roster_correlates_entra_ids(provider, http, response):
    first = response({'members': [{'aadObjectId': 'other'}], 'continuationToken': 'next/page'})
    second = response({'members': [{'id': '29:scoped', 'aadObjectId': USER, 'name': 'Alice',
                                    'userPrincipalName': 'alice@example.test', 'email': 'alice@example.test'}]})
    http.get.side_effect = [first, second]
    profile = await provider.fetch_user(USER)
    assert profile == UserProfile(USER, True, 'Alice', 'alice@example.test', 'alice@example.test')
    assert http.get.call_args.kwargs['params']['continuationToken'] == 'next/page'
    assert '/v3/conversations/team/pagedmembers' in http.get.call_args.args[0]
    first.close.assert_called_once()
    second.close.assert_called_once()
    card = provider._activity(f'<at>{USER}</at> and <at>{USER}</at>')['attachments'][0]['content']
    assert card['body'][0]['text'] == '<at>Alice</at> and <at>Alice</at>'
    assert card['msteams']['entities'] == [
        {'type': 'mention', 'text': '<at>Alice</at>', 'mentioned': {'id': USER, 'name': 'Alice'}},
    ]


@pytest.mark.asyncio
async def test_user_missing_or_lookup_failed(provider, http, response):
    http.get.return_value = response({'members': []})
    assert not (await provider.fetch_user(USER)).exists
    failed = response(status=403)
    http.get.return_value = failed
    assert not (await provider.fetch_user(USER)).exists
    failed.close.assert_called_once()


@pytest.mark.parametrize('changes,title,command', [
    ({'chain_enabled': False, 'status': 'resolved'}, 'Release', 'release'),
    ({'frozen_by_maintenance': True}, 'Maintenance', 'noop'),
    ({'frozen_by_inhibition': True}, 'Inhibited', 'noop'),
    ({'can_unfreeze': True, 'frozen_until_text': 'Tomorrow'}, 'Tomorrow', 'unfreeze'),
    ({'frozen_until': 'tomorrow', 'frozen_until_text': 'Tomorrow'}, 'Tomorrow', 'noop'),
])
def test_action_states(presentation, changes, title, command):
    actions = TeamsProvider._actions(replace(presentation, **changes))
    action = next(action for action in actions if action['title'] == title)
    assert action['data']['command'] == command


def test_freeze_task_and_closed_actions(presentation):
    actions = TeamsProvider._actions(replace(presentation, can_create_task=True))
    assert actions[1]['type'] == 'Action.ShowCard'
    assert len(actions[1]['card']['actions']) == 4
    assert actions[-1]['data'] == {'command': 'task'}
    assert len(TeamsProvider._actions(replace(presentation, can_create_task=True, task_link='https://task'))) == 2
    assert TeamsProvider._actions(replace(presentation, status='closed')) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('command,action,option', [
    ('assign', InteractionAction.ASSIGN, None), ('release', InteractionAction.RELEASE, None),
    ('task', InteractionAction.CREATE_TASK, None), ('unfreeze', InteractionAction.UNFREEZE, None),
    ('noop', InteractionAction.NOOP, None), ('freeze_tomorrow', InteractionAction.FREEZE, 'tomorrow'),
    ('freeze_next_monday', InteractionAction.FREEZE, 'next_monday'),
    ('freeze_month', InteractionAction.FREEZE, 'month'), ('freeze_6months', InteractionAction.FREEZE, '6months'),
])
async def test_verified_callbacks_map_existing_commands(provider, payload, signing_key, command, action, option):
    payload['value']['command'] = command
    result = await provider.parse_interaction(request(payload, signing_key))
    assert isinstance(result, Interaction)
    assert result.message == MessageRef('room', THREAD)
    assert result.actor_id == USER
    assert result.commands[0].action == action
    assert result.commands[0].freeze_option == option


@pytest.mark.asyncio
@pytest.mark.parametrize('claims', [
    {'aud': 'another-app'}, {'iss': 'https://evil.test'}, {'exp': 1},
    {'nbf': 9999999999}, {'serviceurl': 'https://evil.test'},
])
async def test_callback_claims_fail_closed(provider, payload, signing_key, claims):
    result = await provider.parse_interaction(request(payload, signing_key, **claims))
    assert result == ProviderResponse(status_code=401)


@pytest.mark.asyncio
async def test_callback_signature_and_key_endorsement_fail_closed(provider, payload, signing_key):
    valid = request(payload, signing_key)
    token = valid.headers[0][1].split()[1]
    tampered = token.rsplit('.', 1)[0] + '.' + ('A' if token.rsplit('.', 1)[1][0] != 'A' else 'B') + token.rsplit('.', 1)[1][1:]
    assert await provider.parse_interaction(replace(valid, headers=(('Authorization', 'Bearer ' + tampered),))) == ProviderResponse(status_code=401)
    provider._keys[0]['endorsements'] = ['directline']
    assert await provider.parse_interaction(valid) == ProviderResponse(status_code=401)


@pytest.mark.asyncio
async def test_callback_requires_signed_time_claims_and_rejects_hmac(provider, payload, signing_key):
    claims = {'iss': 'https://api.botframework.com', 'aud': APP, 'exp': int(time.time()) + 600,
              'serviceurl': SERVICE}
    token = jwt.encode(claims, signing_key[0], algorithm='RS256', headers={'kid': 'test-key'})
    incoming = InteractionRequest('POST', (('Authorization', f'Bearer {token}'),), (), json.dumps(payload).encode())
    assert await provider.parse_interaction(incoming) == ProviderResponse(status_code=401)
    token = jwt.encode({**claims, 'nbf': int(time.time()) - 1}, 'not-the-signing-key', algorithm='HS256',
                       headers={'kid': 'test-key'})
    assert await provider.parse_interaction(replace(incoming, headers=(('Authorization', f'Bearer {token}'),))) == ProviderResponse(status_code=401)


@pytest.mark.asyncio
async def test_unknown_key_refresh_has_bounded_frequency(provider, payload, signing_key):
    provider._keys[0]['kid'] = 'old-key'
    provider._refresh_keys = AsyncMock()
    incoming = request(payload, signing_key)
    assert await provider.parse_interaction(incoming) == ProviderResponse(status_code=401)
    provider._refresh_keys.assert_not_awaited()
    provider._keys_updated = time.monotonic() - 301

    async def refresh():
        provider._keys = [signing_key[1]]
        provider._keys_updated = time.monotonic()

    provider._refresh_keys.side_effect = refresh
    assert isinstance(await provider.parse_interaction(incoming), Interaction)
    provider._refresh_keys.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [
    ('tenant', {'id': 'another-tenant'}), ('team', {'id': 'another-team'}), ('channel', {'id': 'another-channel'}),
])
async def test_callback_scope_is_restricted(provider, payload, signing_key, field, value):
    payload['channelData'][field] = value
    assert await provider.parse_interaction(request(payload, signing_key)) == ProviderResponse(status_code=403)


@pytest.mark.asyncio
async def test_callback_url_and_channel_id_restricted(provider, payload, signing_key):
    payload['serviceUrl'] = 'https://evil.test/'
    assert await provider.parse_interaction(request(payload, signing_key)) == ProviderResponse(status_code=403)
    payload['serviceUrl'] = SERVICE
    payload['channelId'] = 'directline'
    assert await provider.parse_interaction(request(payload, signing_key)) == ProviderResponse(status_code=403)


@pytest.mark.asyncio
async def test_missing_auth_malformed_payload_and_nonaction_events(provider, payload, signing_key):
    valid = request(payload, signing_key)
    assert await provider.parse_interaction(replace(valid, headers=())) == ProviderResponse(status_code=401)
    assert await provider.parse_interaction(replace(valid, method='GET')) == ProviderResponse(status_code=405)
    assert await provider.parse_interaction(replace(valid, body=b'{')) == ProviderResponse(status_code=400)
    assert await provider.parse_interaction(replace(valid, body=b'[]')) == ProviderResponse(status_code=400)
    payload['value']['command'] = 'unknown'
    assert await provider.parse_interaction(request(payload, signing_key)) == ProviderResponse(status_code=400)
    payload['type'] = 'conversationUpdate'
    assert await provider.parse_interaction(request(payload, signing_key)) == ProviderResponse()


@pytest.mark.asyncio
async def test_interaction_updates_original_card(provider, presentation, payload, signing_key):
    interaction = await provider.parse_interaction(request(payload, signing_key))
    provider.update_incident = AsyncMock()
    await provider.after_interaction(replace(presentation, thread_id=THREAD), interaction)
    provider.update_incident.assert_awaited_once()
    assert provider.respond_to_interaction(presentation) == ProviderResponse()


@pytest.mark.asyncio
async def test_token_cache_and_metadata_use_injected_transport(provider, http, response, signing_key):
    provider._token_expires = 0
    token = response({'access_token': 'new-token', 'expires_in': 3600})
    metadata = response({'jwks_uri': 'https://login.botframework.com/v1/.well-known/keys',
                         'id_token_signing_alg_values_supported': ['RS256']})
    keys = response({'keys': [signing_key[1]]})
    http.post.return_value = token
    http.get.side_effect = [metadata, keys]
    await provider.activate()
    assert (await provider._headers()) == {'Authorization': 'Bearer new-token'}
    assert http.post.await_count == 1
    assert http.post.call_args.kwargs['data']['scope'] == 'https://api.botframework.com/.default'
    assert http.get.call_args_list[0].args[0] == OPENID_URL
    assert all('headers' not in call.kwargs for call in http.get.call_args_list)
    for item in (token, metadata, keys):
        item.close.assert_called_once()


@pytest.mark.asyncio
async def test_key_metadata_cannot_redirect_to_arbitrary_host(provider, http, response):
    http.get.return_value = response({'jwks_uri': 'https://evil.test/keys',
                                    'id_token_signing_alg_values_supported': ['RS256']})
    with pytest.raises(ValueError, match='signing-key URL'):
        await provider._refresh_keys()
    assert http.get.await_count == 1


def test_templates_are_packaged_and_mention_ids_preserved():
    environment = Environment()
    for name in REQUIRED_TEMPLATE_NAMES:
        environment.parse(TeamsProvider.template_source(name))
    template = environment.from_string(TeamsProvider.template_source('chain_step_user'))
    text = template.render(step={'value': 'Alice'}, users={'Alice': {'exists': True, 'id': USER}})
    assert f'<at>{USER}</at>' in text
    with pytest.raises(KeyError):
        TeamsProvider.template_source('../README')


@pytest.mark.asyncio
async def test_ui_oauth_returns_same_entra_identity(http, response):
    auth = TeamsAuthentication('client', 'secret', TENANT)
    url = auth.build_authorization_url('state', 'https://impulse.test/auth/callback')
    assert url.startswith(f'https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?')
    params = parse_qs(urlsplit(url).query)
    assert params['state'] == ['state']
    assert params['scope'] == ['https://graph.microsoft.com/User.Read']
    token = response({'access_token': 'delegated-token'})
    profile = response({'id': USER, 'userPrincipalName': 'alice@example.test', 'displayName': 'Alice'})
    http.post.return_value = token
    http.get.return_value = profile
    user = await auth.authenticate_callback({'code': 'code'}, 'https://impulse.test/auth/callback', http)
    assert user.id == USER
    assert user.full_name == 'Alice'
    assert http.get.call_args.kwargs['headers'] == {'Authorization': 'Bearer delegated-token'}
    token.close.assert_called_once()
    profile.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('params,error', [({'error': 'denied'}, 'provider_error'), ({}, 'missing_code')])
async def test_ui_oauth_rejects_invalid_callbacks(http, params, error):
    with pytest.raises(AuthenticationError) as caught:
        await TeamsAuthentication('client', 'secret', TENANT).authenticate_callback(params, 'redirect', http)
    assert caught.value.code == error
    http.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_ui_oauth_rejects_missing_tokens_and_failed_responses(http, response):
    auth = TeamsAuthentication('client', 'secret', TENANT)
    http.post.return_value = response({})
    with pytest.raises(AuthenticationError, match='access token'):
        await auth.authenticate_callback({'code': 'code'}, 'redirect', http)
    failed = response(status=403)
    http.post.return_value = failed
    with pytest.raises(AuthenticationError):
        await auth.authenticate_callback({'code': 'code'}, 'redirect', http)
    failed.close.assert_called_once()
