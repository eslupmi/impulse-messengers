# IMPulse messengers

Three independently buildable Python libraries provide the Slack, Mattermost
and Telegram integrations for IMPulse. Each owns its configuration schema,
payloads, interactions, authentication and Jinja templates.

| Distribution | Import package | Messenger type |
| --- | --- | --- |
| `impulse-slack` | `impulse_slack` | `slack` |
| `impulse-mattermost` | `impulse_mattermost` | `mattermost` |
| `impulse-telegram` | `impulse_telegram` | `telegram` |

Python 3.10 or newer is required. All providers use the version 1 contract
from `impulse_messenger_api`, supplied by `impulse-bot==3.8.0`, and register
through the `impulse.messengers` entry-point group. Installing a provider makes
it available to IMPulse; select it with the existing `messenger.type` setting.
The `none` provider remains in IMPulse and needs no messenger library.

## Release versions

The next release is `3.8.0` for IMPulse and all three messenger libraries.
Core and provider versions must match: each `3.8.0` library requires
`impulse-bot==3.8.0`. Messenger libraries have no independent version bumps;
update a library to the target IMPulse version only when that IMPulse release
requires library changes.

## Local development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and keep
the two repositories beside each other:

```text
parent/
├── impulse/
└── impulse-messengers/
```

From `impulse-messengers`, install all three libraries and the sibling IMPulse
checkout in editable mode using the shared lockfile:

```sh
uv sync --all-packages --locked
uv run --all-packages python -c "from importlib.metadata import entry_points; print([ep.name for ep in entry_points(group='impulse.messengers')])"
```

The root project is a development workspace and is not distributed. Dependencies
are declared in each library's `pyproject.toml`; `uv.lock` records the combined
resolution. After changing dependencies, run `uv lock` and repeat `uv sync`.
The sibling path in `tool.uv.sources` is a development override: wheel metadata
contains the `impulse-bot` version requirement, without a checkout path.

IMPulse's existing provider tests live in the sibling repository. Run them from
its directory after its documented `uv sync` preparation.

## Build

Build all three libraries as separate wheels and source distributions:

```sh
uv build --all-packages
```

Build one independently:

```sh
uv build --package impulse-slack
uv build --package impulse-mattermost
uv build --package impulse-telegram
```

Artifacts are written to `dist/`. Each contains its own 13 templates and license.
Install the selected wheel alongside an `impulse-bot` wheel in a fresh environment
to check package discovery without editable source paths.

Run the repeatable installed-package verification from this repository:

```sh
python3 scripts/verify-packages.py
# Equivalent after local preparation:
uv run --no-sync python scripts/verify-packages.py
```

Use `--uv /path/to/uv` or `--core /path/to/impulse` when needed. The script builds
all four distributions, rebuilds wheels from their source archives, and creates
temporary environments containing core alone or core plus one provider. It checks
matching release versions and exact core dependency pins, isolated discovery,
configuration, `python -I -m main --check`, bundled static/Jira
resources, all provider templates, delivery through the real core facade with a
fake HTTP transport, malformed callbacks, response cleanup and provider removal.
It uses no messenger accounts or live provider requests. Logs and artifacts remain
on failure; `--keep-artifacts` also preserves successful evidence.

## Continuous integration

The `Verify messenger libraries` workflow runs linting, verification-harness
regression tests and the same installed-package verification on pushes, pull
requests and manual dispatches, using Python 3.10, uv 0.12.22 and Ruff 0.16.10
(the version in IMPulse's lockfile).
It checks out this repository alongside `eslupmi/impulse` at `messenger-split`.
For a coordinated change on another core revision, manually dispatch the workflow
with its branch, tag or commit in `core_ref`.

Both repositories' extraction changes must be committed and pushed to their
selected refs before hosted CI can pass. The selected IMPulse revision must
contain `impulse-bot` packaging and `impulse_messenger_api`; the workflow reports
a clear error if it does not. When selecting a release baseline, update both
the default checkout ref and the `core_ref` input default to the compatible
core tag or commit. This workflow builds and verifies packages without publishing.
On failure, the hosted job output includes the failing command's log path and
final 15 lines; full files remain only on the temporary runner until it is removed.

Publication is a separate step; the prepared `3.8.0` libraries have not been
published by this extraction.
