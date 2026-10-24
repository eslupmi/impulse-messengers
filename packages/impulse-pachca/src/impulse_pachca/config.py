from typing import Literal

from pydantic import BaseModel, Field, model_validator

from impulse_messenger_api import BaseApplicationConfig, BaseUser


class PachcaUser(BaseUser):
    id: int = Field(..., gt=0, description='User ID')


class PachcaChannel(BaseModel):
    id: int = Field(..., gt=0, description='Chat ID')


class PachcaGroup(BaseModel):
    id: int = Field(..., gt=0, description='Group tag ID')


class PachcaApplicationConfig(BaseApplicationConfig):
    type: Literal['pachca'] = Field('pachca', description='Application type')
    channels: dict[str, PachcaChannel] = Field(..., description='Channel definitions')
    groups: dict[str, PachcaGroup] = Field({}, description='Pachca group tag definitions')
    users: dict[str, PachcaUser] = Field(..., description='User definitions')

    @model_validator(mode='after')
    def validate_impulse_address_required(self):
        if not self.impulse_address:
            raise ValueError(f'messenger.impulse_address is required for {self.type}')
        return self
