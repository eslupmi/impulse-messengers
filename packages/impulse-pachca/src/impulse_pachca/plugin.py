"""Installed Pachca messenger registration."""

from impulse_messenger_api import ProviderRegistration

from . import PachcaProvider
from .authentication import PachcaAuthentication


plugin = ProviderRegistration(
    descriptor=PachcaProvider.descriptor,
    factory=PachcaProvider,
    config_model=PachcaProvider.config_model,
    authentication_factory=lambda client_id, client_secret, messenger: PachcaAuthentication(client_id, client_secret),
    template_source=PachcaProvider.template_source,
    incident_url=PachcaProvider.incident_url,
)
