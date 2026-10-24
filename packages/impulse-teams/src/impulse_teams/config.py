"""Microsoft public-cloud, single-tenant Teams configuration."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from impulse_messenger_api import BaseApplicationConfig, BaseUser, HttpBase

GUID_PATTERN = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'


class TeamsUser(BaseUser):
    id: str = Field(..., pattern=GUID_PATTERN, description='Microsoft Entra user object ID, in lowercase')


class TeamsChannel(BaseModel):
    id: str = Field(..., min_length=1, description='Teams channel ID from Copy link to channel')


class TeamsApplicationConfig(BaseApplicationConfig):
    type: Literal['teams'] = 'teams'
    tenant_id: str = Field(..., pattern=GUID_PATTERN, description='Microsoft Entra tenant ID')
    team_id: str = Field(..., min_length=1, description='Bot Framework team ID from channelData.team.id')
    service_url: HttpBase = Field(..., description='Exact serviceUrl from a Teams bot activity')
    channels: dict[str, TeamsChannel] = Field(..., description='Standard incident channels in this team')
    users: dict[str, TeamsUser] = Field(..., description='User definitions')

    @field_validator('service_url')
    @classmethod
    def validate_service_url(cls, value):
        url = urlsplit(value)
        if (url.scheme != 'https' or url.netloc != 'smba.trafficmanager.net'
                or url.query or url.fragment):
            raise ValueError('Teams service_url must be an HTTPS smba.trafficmanager.net URL without a query or fragment')
        return value

    @model_validator(mode='after')
    def validate_supported_configuration(self):
        if not self.impulse_address:
            raise ValueError('messenger.impulse_address is required for teams')
        if self.groups:
            raise ValueError('Teams native groups are not supported; use messenger.user_groups')
        return self
