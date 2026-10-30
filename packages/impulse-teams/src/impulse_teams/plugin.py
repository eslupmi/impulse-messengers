"""Installed Teams messenger registration."""
from impulse_messenger_api import ProviderRegistration

from . import TeamsProvider
from .authentication import TeamsAuthentication

plugin = ProviderRegistration(
    descriptor=TeamsProvider.descriptor,
    factory=TeamsProvider,
    config_model=TeamsProvider.config_model,
    authentication_factory=lambda client_id, client_secret, messenger: TeamsAuthentication(
        client_id, client_secret, messenger.tenant_id,
    ),
    template_source=TeamsProvider.template_source,
    incident_url=TeamsProvider.incident_url,
)
