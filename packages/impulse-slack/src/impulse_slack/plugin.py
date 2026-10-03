"""Installed Slack messenger registration."""

from impulse_messenger_api import ProviderRegistration

from . import SlackProvider
from .authentication import SlackAuthentication


plugin = ProviderRegistration(
    descriptor=SlackProvider.descriptor,
    factory=SlackProvider,
    config_model=SlackProvider.config_model,
    authentication_factory=lambda client_id, client_secret, messenger: SlackAuthentication(client_id, client_secret),
    template_source=SlackProvider.template_source,
    incident_url=SlackProvider.incident_url,
)
