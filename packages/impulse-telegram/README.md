# impulse-telegram

Telegram messenger provider for IMPulse, including its configuration schema,
message payloads, interaction handling, authentication and notification templates.

Version `3.8.0` requires Python 3.10 or newer and `impulse-bot==3.8.0`. IMPulse discovers
the installed provider through the `impulse.messengers` entry point.

This library follows IMPulse release numbers without independent version bumps.
Update it to the target IMPulse version only when that release requires library changes.

Build a wheel and source distribution with `uv build --package impulse-telegram`
from the repository root. See the repository README for local development.
