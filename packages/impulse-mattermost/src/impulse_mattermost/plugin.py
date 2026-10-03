"""Installed Mattermost messenger registration."""

from impulse_messenger_api import ProviderRegistration

from . import MattermostProvider
from .authentication import MattermostAuthentication


plugin = ProviderRegistration(
    descriptor=MattermostProvider.descriptor,
    factory=MattermostProvider,
    config_model=MattermostProvider.config_model,
    authentication_factory=lambda client_id, client_secret, messenger: MattermostAuthentication(
        messenger.address, client_id, client_secret),
    template_source=MattermostProvider.template_source,
    incident_url=MattermostProvider.incident_url,
)
