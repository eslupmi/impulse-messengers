import ast
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from jinja2 import Environment
import pytest

from app.im.registry import ProviderRegistry
from impulse_messenger_api import REQUIRED_TEMPLATE_NAMES


@pytest.mark.parametrize('provider_id', ['pachca', 'rocketchat', 'teams'])
def test_registration_and_packaged_templates_match_core(provider_id):
    plugin = import_module(f'impulse_{provider_id}.plugin').plugin
    registry = ProviderRegistry()
    registry.register(plugin)
    assert registry.resolve(provider_id) is plugin
    environment = Environment()
    for name in REQUIRED_TEMPLATE_NAMES:
        environment.parse(plugin.template_source(name))
    with pytest.raises(KeyError):
        plugin.template_source('not_a_template')


@pytest.mark.parametrize('provider_id', ['pachca', 'rocketchat', 'teams'])
def test_providers_depend_on_public_contract_only(provider_id):
    root = Path(__file__).resolve().parents[1] / 'packages' / f'impulse-{provider_id}' / 'src'
    for source in root.rglob('*.py'):
        for node in ast.walk(ast.parse(source.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or '']
            else:
                continue
            assert all(module != 'app' and not module.startswith('app.') for module in modules), source


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_id', ['pachca', 'rocketchat', 'teams'])
async def test_core_facade_initializes_creates_updates_and_notifies(provider_id, monkeypatch, http, response):
    runtime_config = SimpleNamespace(
        app=SimpleNamespace(task_management=None, general=SimpleNamespace(timezone='UTC')),
        ui_config=Mock(), INCIDENT_ACTUAL_VERSION='v3.8.0',
    )
    monkeypatch.setattr('app.config.config.get_config', lambda: runtime_config)
    from app.im.helpers import get_application

    plugin = import_module(f'impulse_{provider_id}.plugin').plugin
    registry = ProviderRegistry()
    registry.register(plugin)
    monkeypatch.setattr('app.im.helpers.get_provider_registry', lambda: registry)
    env = SimpleNamespace(dev_messenger_custom_address=None, task_management_enabled=False)
    monkeypatch.setattr('app.im.helpers.get_environment_config', lambda: env)
    monkeypatch.setattr('app.im.application.get_environment_config', lambda: env)
    monkeypatch.setattr('app.im.application.get_config', lambda: runtime_config)
    store = Mock()
    store.get_all_users_by_type.return_value = {}
    monkeypatch.setattr('app.im.application.get_user_store', lambda: store)
    for secret in ('PACHCA_BOT_TOKEN', 'PACHCA_SIGNING_SECRET', 'ROCKETCHAT_AUTH_TOKEN',
                   'ROCKETCHAT_USER_ID', 'ROCKETCHAT_WEBHOOK_TOKEN', 'TEAMS_APP_ID', 'TEAMS_APP_SECRET'):
        monkeypatch.setenv(secret, 'test-secret')
    channel_id = 334 if provider_id == 'pachca' else 'room'
    options = {}
    if provider_id == 'rocketchat':
        options['address'] = 'https://rocket.example'
    elif provider_id == 'teams':
        options.update(tenant_id='11111111-1111-1111-1111-111111111111', team_id='team',
                       service_url='https://smba.trafficmanager.net/emea')
    config = plugin.config_model(impulse_address='https://impulse.example', admin_users=[], users={},
                                 channels={'default': {'id': channel_id}}, **options)
    app = get_application(config, {'default': {'id': channel_id}}, 'default')
    app._setup_http = Mock(return_value=http)
    http.close = AsyncMock()
    replies = []

    async def request(url, **kwargs):
        if url.endswith('/token'):
            data = {'access_token': 'access', 'expires_in': 3600}
        elif url.endswith('/openidconfiguration'):
            data = {'jwks_uri': 'https://login.botframework.com/keys',
                    'id_token_signing_alg_values_supported': ['RS256']}
        elif url.endswith('/keys'):
            data = {'keys': []}
        elif url.endswith('/chat.sendMessage'):
            data = {'success': True, 'message': {'_id': kwargs['json']['message']['_id']}}
        else:
            data = {'success': True, 'data': {'id': 123}, 'id': 'conversation', 'activityId': 'activity'}
        reply = response(data)
        replies.append(reply)
        return reply

    http.get.side_effect = http.post.side_effect = http.put.side_effect = request
    await app.initialize_async()
    incident = SimpleNamespace(
        channel_id=channel_id, ts='', status='firing', chain_enabled=True, is_frozen=False,
        frozen_by_inhibition=False, frozen_by_maintenance=False, frozen_until=None,
        can_manual_unfreeze=lambda: False, task_link='', assigned_user_id='',
    )
    incident.ts = await app.create_incident_message(incident, 'body', 'header', '🔥')
    assert isinstance(incident.ts, str) and incident.ts
    app.form_body_header_status_icons = Mock(return_value=('body', 'header', '🔥'))
    await app.update_incident_message(incident)
    assert await app._post_notification(incident, 'header', 'notice') == 200
    assert all(reply.close.called for reply in replies)
    await app.close()
    http.close.assert_awaited_once()
