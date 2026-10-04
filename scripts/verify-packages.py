#!/usr/bin/env python3
"""Build and exercise independent wheels in disposable, isolated environments."""

import argparse
import asyncio
import importlib
from email.parser import Parser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from urllib.parse import urlsplit
from zipfile import ZipFile


PROVIDERS = ('slack', 'mattermost', 'telegram')
FIXTURE_SECRETS = {
    'SLACK_BOT_USER_OAUTH_TOKEN': 'fixture-token', 'SLACK_VERIFICATION_TOKEN': 'fixture-verification',
    'MATTERMOST_ACCESS_TOKEN': 'fixture-token', 'TELEGRAM_BOT_TOKEN': 'fixture-token',
}
REPOSITORY = Path(__file__).resolve().parents[1]


def configuration(provider_id):
    messenger = {'type': provider_id}
    if provider_id != 'none':
        messenger.update(
            channels={'default': {'id': -100123 if provider_id == 'telegram' else 'C1'}},
            users={}, admin_users=[], impulse_address='http://impulse.test',
        )
        if provider_id == 'mattermost':
            messenger.update(address='https://mattermost.test', team='test-team')
    return {'messenger': messenger, 'route': {'channel': 'default'}, 'ui': {'columns': []}}


def installed_module(name):
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), name
    return module


def probe(provider_id):
    from importlib.metadata import entry_points, version

    core_version = version('impulse-bot')
    if provider_id != 'none':
        provider_version = version(f'impulse-{provider_id}')
        assert provider_version == core_version, (
            f'Release version mismatch: impulse-{provider_id}={provider_version}; impulse-bot={core_version}'
        )

    api = installed_module('impulse_messenger_api')
    assert Path(api.__file__).with_name('py.typed').is_file()
    installed_module('app')
    from app.im.registry import get_provider_registry
    from app.config.validation import validate_config
    from app.resources import resource_directory
    from app.integrations.jira_integration import JiraIntegration

    expected = set() if provider_id == 'none' else {provider_id}
    assert {entry.name for entry in entry_points(group='impulse.messengers')} == expected
    registry = get_provider_registry()
    assert registry.resolve('none').descriptor.messaging_enabled is False
    assert type(validate_config(configuration('none')).messenger.type) is str
    for missing in set(PROVIDERS) - expected:
        try:
            registry.resolve(missing)
        except ValueError as error:
            assert 'Install a compatible provider distribution' in str(error)
        else:
            raise AssertionError(f'{missing} unexpectedly registered')
        try:
            validate_config(configuration(missing))
        except ValueError as error:
            assert 'messenger provider is not registered' in str(error)
        else:
            raise AssertionError(f'{missing} configuration unexpectedly accepted')

    for name in ('static', 'templates'):
        path = resource_directory(name).resolve()
        assert path.is_relative_to(Path(sys.prefix).resolve()), path
    assert (resource_directory('static') / 'index.html').read_text(encoding='utf-8')
    jira = JiraIntegration(None)
    for name in ('summary', 'description'):
        assert jira._read_template(name).template

    if provider_id != 'none':
        asyncio.run(probe_provider(provider_id, registry))


def probe_selection(provider_id):
    from importlib.metadata import entry_points
    from app.config.validation import validate_config
    from app.im.helpers import get_application

    assert {entry.name for entry in entry_points(group='impulse.messengers')} == set(PROVIDERS)
    config = validate_config(configuration(provider_id)).messenger
    os.environ.update(FIXTURE_SECRETS)
    channel_id = 'default' if provider_id == 'none' else config.channels['default'].id
    app = get_application(config, {'default': {'id': channel_id}}, 'default')
    assert app.type == app.provider.descriptor.provider_id == provider_id
    installed_module('main')
    loaded = {provider for provider in PROVIDERS if f'impulse_{provider}' in sys.modules}
    expected = set() if provider_id == 'none' else {provider_id}
    assert loaded == expected, f'{provider_id} startup imported unexpected providers: {loaded}'


class FakeResponse:
    def __init__(self, body):
        self.body, self.status, self.closed = body, 200, False

    async def json(self):
        return self.body

    def close(self):
        self.closed = True


class FakeTransport:
    """The public transport seam; no HTTP session or live network is used."""

    def __init__(self, provider_id):
        self.responses, self.calls, self.closed = [], [], False
        self.routes = {
            'slack': {
                ('GET', 'https://slack.com/api/auth.test'): {'ok': True, 'url': 'https://workspace.slack.test/'},
                ('GET', 'https://slack.com/api/users.info'): {
                    'ok': True, 'user': {'name': 'alice', 'tz': 'UTC', 'profile': {'real_name_normalized': 'Alice'}},
                },
                ('GET', 'https://slack.com/api/usergroups.list'): {'ok': True, 'usergroups': []},
                ('POST', 'https://slack.com/api/chat.postMessage'): {'ok': True, 'ts': '123.456'},
                ('POST', 'https://slack.com/api/chat.update'): {'ok': True},
            },
            'mattermost': {
                ('GET', 'https://mattermost.test/api/v4/users/U1?user_id=U1'): {
                    'username': 'alice', 'first_name': 'Alice',
                    'timezone': {'useAutomaticTimezone': 'true', 'automaticTimezone': 'UTC'},
                },
                ('POST', 'https://mattermost.test/api/v4/posts'): {'id': 'post-1'},
                ('PUT', 'https://mattermost.test/api/v4/posts/post-1'): {'id': 'post-1'},
            },
            'telegram': {
                ('POST', 'https://api.telegram.org/botfixture-token/setWebhook'): {'ok': True, 'result': True},
                ('GET', 'https://api.telegram.org/botfixture-token/getChat?chat_id=123'): {
                    'ok': True, 'result': {'username': 'alice', 'first_name': 'Alice'},
                },
                ('POST', 'https://api.telegram.org/botfixture-token/createForumTopic'): {
                    'ok': True, 'result': {'message_thread_id': 10},
                },
                ('POST', 'https://api.telegram.org/botfixture-token/sendMessage'): {
                    'ok': True, 'result': {'message_id': 20},
                },
                ('POST', 'https://api.telegram.org/botfixture-token/editForumTopic'): {'ok': True, 'result': True},
                ('POST', 'https://api.telegram.org/botfixture-token/editMessageText'): {'ok': True, 'result': {}},
            },
        }[provider_id]

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if (method, url) not in self.routes:
            raise AssertionError(f'Unexpected provider request: {method} {url}')
        response = FakeResponse(self.routes[method, url])
        self.responses.append(response)
        return response

    async def get(self, url, **kwargs):
        return await self.request('GET', url, **kwargs)

    async def post(self, url, **kwargs):
        return await self.request('POST', url, **kwargs)

    async def put(self, url, **kwargs):
        return await self.request('PUT', url, **kwargs)

    async def close(self):
        self.closed = True


async def probe_provider(provider_id, registry):
    from jinja2 import Environment
    from impulse_messenger_api import REQUIRED_TEMPLATE_NAMES, InteractionRequest
    from app.config.validation import validate_config
    from app.im.application import Application
    from app.im.helpers import get_application

    module = installed_module(f'impulse_{provider_id}')
    assert Path(module.__file__).with_name('py.typed').is_file()
    registration = registry.resolve(provider_id)
    config = validate_config(configuration(provider_id)).messenger
    assert type(config) is registration.config_model
    assert type(config.type) is str
    from app.im.user_store import UserStore
    import yaml
    saved = yaml.safe_dump(UserStore.serialize(config.type, {'id': 'fixture-user'}))
    assert yaml.safe_load(saved)['messenger_type'] == provider_id
    for name in REQUIRED_TEMPLATE_NAMES:
        Environment().parse(registration.template_source(name))
    resources = Path(module.__file__).parent / 'resources'
    assert len(list(resources.glob('*.j2'))) == len(REQUIRED_TEMPLATE_NAMES) == 13

    try:
        registration.factory(config, {})
    except ValueError as error:
        assert 'is required' in str(error)
    else:
        raise AssertionError('Provider accepted missing credentials')

    os.environ.update(FIXTURE_SECRETS)
    app = get_application(config, {'default': {'id': config.channels['default'].id}}, 'default')
    assert type(app) is Application and type(app.provider) is registration.factory
    transport = FakeTransport(provider_id)
    app._setup_http = lambda: transport
    try:
        await app.initialize_async()
        assert app.provider.http is transport
        user = await app.get_user_details(123 if provider_id == 'telegram' else 'U1')
        assert user['exists'] and user['username'] == 'alice'
        await app.provider.fetch_groups()
        incident = SimpleNamespace(
            channel_id=config.channels['default'].id, ts='', status='firing',
            chain_enabled=True, is_frozen=False, frozen_by_inhibition=False,
            frozen_by_maintenance=False, frozen_until=None, can_manual_unfreeze=lambda: False,
            task_link='', assigned_user_id='', payload={}, uniq_id='fixture-incident',
        )
        delivery_start = len(transport.calls)
        incident.ts = await app.create_incident_message(incident, 'body', 'header', '5312241539987020022')
        assert incident.ts
        app.form_body_header_status_icons = lambda _: ('body', 'header', '5312241539987020022')
        await app.update_incident_message(incident)
        assert await app._post_notification(incident, 'header', 'notice') == 200
        delivery_calls = transport.calls[delivery_start:]
        assert [urlsplit(url).path.rsplit('/', 1)[-1] for _, url, _ in delivery_calls] == {
            'slack': ['chat.postMessage', 'chat.update', 'chat.postMessage'],
            'mattermost': ['posts', 'post-1', 'posts'],
            'telegram': ['createForumTopic', 'sendMessage', 'editForumTopic', 'editMessageText', 'sendMessage'],
        }[provider_id], delivery_calls
        notification = delivery_calls[-1][2]['json']
        assert notification.get('text', notification.get('message')) == (
            'notice' if provider_id == 'telegram' else 'header\nnotice'
        ), notification
        response = await app.buttons_handler(InteractionRequest('POST', (), (), b'not-json'), None, None)
        assert response.status_code == 400
        assert all(response.closed for response in transport.responses)
        if provider_id == 'telegram':
            webhook = next(kwargs for _, url, kwargs in transport.calls if url.endswith('/setWebhook'))
            assert webhook['params'] == {'url': 'http://impulse.test/app'}
    finally:
        await app.close()
    assert transport.closed


def verify(uv, core, keep_artifacts):
    temporary = Path(tempfile.mkdtemp(prefix='impulse-package-verification-'))
    log_number = 0
    environment = {key: value for key, value in os.environ.items() if not key.startswith(
        ('PYTHON', 'AUTH_', 'SLACK_', 'MATTERMOST_', 'TELEGRAM_', 'JIRA_', 'DEV_MESSENGER_'))}
    environment.pop('VIRTUAL_ENV', None)
    environment.pop('UV_PROJECT_ENVIRONMENT', None)
    environment.update(CONFIG_PATH=str(temporary / 'config'), DATA_PATH=str(temporary / 'data'))
    work = temporary / 'work'
    work.mkdir()
    Path(environment['CONFIG_PATH']).mkdir()
    config_file = Path(environment['CONFIG_PATH']) / 'impulse.yml'
    config_file.write_text(json.dumps(configuration('none')), encoding='utf-8')

    def run(command, cwd=work):
        nonlocal log_number
        log_number += 1
        log = temporary / f'{log_number:02d}.log'
        with log.open('w', encoding='utf-8') as output:
            output.write(f'cwd: {cwd}\ncommand: {command!r}\n')
            output.flush()
            result = subprocess.run(command, cwd=cwd, env=environment, stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            tail = '\n'.join(log.read_text(encoding='utf-8', errors='replace').splitlines()[-15:])
            raise RuntimeError(f'Command failed; full log: {log}\n{tail}')

    try:
        dist = temporary / 'dist'
        run([uv, 'build', '--sdist', '--out-dir', str(dist)], cwd=core)
        run([uv, 'build', '--all-packages', '--sdist', '--out-dir', str(dist)], cwd=REPOSITORY)
        rebuilt = temporary / 'rebuilt'
        for archive in sorted(dist.glob('*.tar.gz')):
            run([uv, 'build', str(archive), '--wheel', '--no-sources', '--out-dir', str(rebuilt)])
        wheels = {wheel.name.split('-')[0]: wheel for wheel in rebuilt.glob('*.whl')}
        assert set(wheels) == {'impulse_bot', *(f'impulse_{provider}' for provider in PROVIDERS)}, wheels
        release_metadata = {}
        for name, wheel in wheels.items():
            with ZipFile(wheel) as archive:
                metadata_name = next(item for item in archive.namelist() if item.endswith('.dist-info/METADATA'))
                metadata = Parser().parsestr(archive.read(metadata_name).decode())
                release_metadata[name] = metadata
                requirements = metadata.get_all('Requires-Dist', [])
                assert all(' @ ' not in line and 'file:' not in line for line in requirements), requirements
                if name == 'impulse_bot':
                    assert not {'app/im/plugin_api.py', 'app/im/plugin_config.py', 'app/im/providers/legacy.py', 'app/im/colors.py'} & set(
                        archive.namelist()
                    )
                    assert not any(item.startswith(tuple(f'app/im/providers/{provider}/' for provider in PROVIDERS))
                                   for item in archive.namelist())
                else:
                    assert sum(item.startswith(name + '/resources/') and item.endswith('.j2')
                               for item in archive.namelist()) == 13
        core_version = release_metadata['impulse_bot']['Version']
        assert core_version, 'Core wheel is missing its release version'
        for name, metadata in release_metadata.items():
            assert metadata['Version'] == core_version, (
                f"Release version mismatch: {name}={metadata['Version']}; impulse-bot={core_version}"
            )
            if name != 'impulse_bot':
                core_requirements = [requirement for requirement in metadata.get_all('Requires-Dist', [])
                                     if requirement.startswith('impulse-bot')]
                assert core_requirements == [f'impulse-bot=={core_version}'], (
                    f'{name} must pin its matching core release: {core_requirements}'
                )
        print(f'PASS four independent sdists rebuilt into wheels at release {core_version}', flush=True)

        script = str(Path(__file__).resolve())
        standalone_root = temporary / 'standalone-core'
        shutil.unpack_archive(str(next(dist.glob('impulse_bot-*.tar.gz'))), str(standalone_root))
        standalone = next(standalone_root.iterdir())
        assert not (standalone.parent / 'impulse-messengers').exists()
        shutil.copyfile(core / 'uv.lock', standalone / 'uv.lock')
        run([uv, 'lock', '--check'], cwd=standalone)
        run([uv, 'sync', '--locked', '--python', sys.executable], cwd=standalone)
        source_python = standalone / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        run([str(source_python), '-I', '-c',
             'from importlib.metadata import entry_points; from app.im.registry import get_provider_registry; '
             'assert not entry_points(group="impulse.messengers"); '
             'assert get_provider_registry().resolve("none").descriptor.messaging_enabled is False'])
        run([str(source_python), '-I', '-m', 'main', '--check'])
        run([str(source_python), '-m', 'ruff', 'check', 'app', 'impulse_messenger_api', 'main.py',
             '--target-version', 'py310'], cwd=standalone)
        print('PASS standalone core source: locked sync, none discovery, CLI and lint without messenger checkout', flush=True)

        for provider_id in ('none', *PROVIDERS):
            config_file.write_text(json.dumps(configuration(provider_id)), encoding='utf-8')
            virtualenv = temporary / provider_id
            run([uv, 'venv', str(virtualenv), '--python', sys.executable])
            python = virtualenv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            selected = [str(wheels['impulse_bot'])]
            if provider_id != 'none':
                selected.append(str(wheels[f'impulse_{provider_id}']))
            run([uv, 'pip', 'install', '--python', str(python), *selected])
            run([str(python), '-I', script, '--probe', provider_id])
            run([str(python), '-I', '-m', 'main', '--check'])
            if provider_id != 'none':
                run([uv, 'pip', 'uninstall', '--python', str(python), f'impulse-{provider_id}'])
                config_file.write_text(json.dumps(configuration('none')), encoding='utf-8')
                run([str(python), '-I', script, '--probe', 'none'])
            print(f'PASS {provider_id}: isolated discovery, config, CLI, resources'
                  + (', fake facade lifecycle, uninstall' if provider_id != 'none' else ''), flush=True)

        python = temporary / 'none' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        run([uv, 'pip', 'install', '--python', str(python), *(str(wheels[f'impulse_{provider}']) for provider in PROVIDERS)])
        for provider_id in ('none', *PROVIDERS):
            config_file.write_text(json.dumps(configuration(provider_id)), encoding='utf-8')
            run([str(python), '-I', script, '--probe-selection', provider_id])
            print(f'PASS {provider_id}: only the configured provider imported with all three installed', flush=True)
    except BaseException:
        print(f'Verification evidence retained at {temporary}', file=sys.stderr)
        raise
    else:
        if keep_artifacts:
            print(f'Verification evidence retained at {temporary}')
        else:
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uv', default='uv', help='uv executable (default: uv on PATH)')
    parser.add_argument('--core', type=Path, default=REPOSITORY.parent / 'impulse', help='IMPulse checkout')
    parser.add_argument('--keep-artifacts', action='store_true', help='retain successful build artifacts and logs')
    parser.add_argument('--probe', choices=('none', *PROVIDERS), help=argparse.SUPPRESS)
    parser.add_argument('--probe-selection', choices=('none', *PROVIDERS), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.probe:
        probe(args.probe)
    elif args.probe_selection:
        probe_selection(args.probe_selection)
    else:
        verify(args.uv, args.core.resolve(), args.keep_artifacts)


if __name__ == '__main__':
    main()
