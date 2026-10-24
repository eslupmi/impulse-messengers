# IMPulse Messengers

Python libraries provide the Slack, Mattermost, Telegram, Pachca, Rocket.Chat and Microsoft Teams
integrations for IMPulse through the existing `impulse.messengers` entry points.

See each provider's setup, supported features and platform limitations:

- [Pachca](packages/impulse-pachca/README.md)
- [Rocket.Chat](packages/impulse-rocketchat/README.md)
- [Microsoft Teams](packages/impulse-teams/README.md)

Install the chosen library into IMPulse's Python 3.12 environment, then set `messenger.type`
to its registered name (`pachca`, `rocketchat` or `teams`). IMPulse supplies
`impulse_messenger_api`; these packages use that contract without importing core internals.

## Build

Build one library as a wheel and a source distribution:

```sh
uv build --package impulse-slack
uv build --package impulse-mattermost
uv build --package impulse-telegram
uv build --package impulse-pachca
uv build --package impulse-rocketchat
uv build --package impulse-teams
```

## Verify

CI installs these libraries and runs the test suite from the IMPulse core repository.
Keep provider-specific validation checks temporary and outside this repository.

```sh
uv sync --all-packages --group dev
uv run --no-sync ruff check packages/
```

Temporary provider checks use mocked HTTP responses. Live bot installation, permissions, message rendering,
notifications, button delivery and OAuth login must also be checked in each target workspace.
