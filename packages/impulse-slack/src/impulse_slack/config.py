from pydantic import BaseModel, Field
from typing import Literal
from impulse_messenger_api import BaseApplicationConfig, BaseUser

class SlackUser(BaseUser):
    id: str

class SlackChannel(BaseModel):
    id: str

class SlackGroup(BaseModel):
    id: str

class SlackApplicationConfig(BaseApplicationConfig):
    """Slack messenger configuration"""
    type: Literal['slack'] = Field('slack', description="Application type")
    channels: dict[str, SlackChannel] = Field(..., description="Channel definitions")
    groups: dict[str, SlackGroup] = Field({}, description="Slack group definitions")
    users: dict[str, SlackUser] = Field(..., description="User definitions")
