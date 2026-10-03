# impulse-telegram

Telegram messenger provider for IMPulse, including its configuration schema,
message payloads, interaction handling, authentication and notification templates.

Requires Python 3.10 or newer and `impulse-bot>=3.7.1,<4.0`. IMPulse discovers
the installed provider through the `impulse.messengers` entry point.

Build a wheel and source distribution with `uv build --package impulse-telegram`
from the repository root. See the repository README for local development.
