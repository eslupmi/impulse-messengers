from typing import Literal

from pydantic import BaseModel, Field, model_validator

from impulse_messenger_api import BaseApplicationConfig, BaseUser, HttpBase


class RocketChatUser(BaseUser):
    id: str = Field(..., min_length=1, description='Rocket.Chat user ID')


class RocketChatChannel(BaseModel):
    id: str = Field(..., min_length=1, description='Rocket.Chat room ID')


class RocketChatGroup(BaseModel):
    id: str = Field(..., min_length=1, description='Rocket.Chat team ID')


class RocketChatApplicationConfig(BaseApplicationConfig):
    type: Literal['rocketchat'] = 'rocketchat'
    address: HttpBase = Field(..., description='Rocket.Chat workspace URL')
    channels: dict[str, RocketChatChannel]
    users: dict[str, RocketChatUser]
    groups: dict[str, RocketChatGroup] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_impulse_address_required(self):
        if not self.impulse_address:
            raise ValueError('messenger.impulse_address is required for rocketchat')
        return self
