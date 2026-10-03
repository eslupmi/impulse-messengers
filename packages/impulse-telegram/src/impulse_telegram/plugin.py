"""Installed Telegram messenger registration."""

from impulse_messenger_api import ProviderRegistration

from . import TelegramProvider
from .authentication import TelegramAuthentication


plugin = ProviderRegistration(
    descriptor=TelegramProvider.descriptor,
    factory=TelegramProvider,
    config_model=TelegramProvider.config_model,
    authentication_factory=lambda client_id, client_secret, messenger: TelegramAuthentication(client_id, client_secret),
    template_source=TelegramProvider.template_source,
    incident_url=TelegramProvider.incident_url,
)
