"""Installed Rocket.Chat messenger registration."""
from impulse_messenger_api import ProviderRegistration

from . import RocketChatProvider
from .authentication import RocketChatAuthentication


plugin = ProviderRegistration(
    descriptor=RocketChatProvider.descriptor,
    factory=RocketChatProvider,
    config_model=RocketChatProvider.config_model,
    authentication_factory=lambda client_id, client_secret, messenger: RocketChatAuthentication(
        messenger.address, client_id, client_secret),
    template_source=RocketChatProvider.template_source,
    incident_url=RocketChatProvider.incident_url,
)
