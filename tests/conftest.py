from pathlib import Path
import sys
from unittest.mock import AsyncMock, Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
# Exercise the actual public contract supplied by the sibling IMPulse checkout.
sys.path.insert(0, str(ROOT.parent / 'impulse'))
for package in (ROOT / 'packages').glob('*/src'):
    sys.path.insert(0, str(package))

from impulse_messenger_api import IncidentPresentation  # noqa: E402


@pytest.fixture
def response():
    def make(data=None, status=200):
        return Mock(status=status, json=AsyncMock(return_value=data), close=Mock())
    return make


@pytest.fixture
def http():
    return Mock(get=AsyncMock(), post=AsyncMock(), put=AsyncMock())


@pytest.fixture
def presentation():
    return IncidentPresentation(
        channel_id='room', thread_id='message', status='firing', header='Alert', body='Details',
        status_icon='🔥', chain_enabled=True, frozen=False, frozen_by_inhibition=False,
        frozen_by_maintenance=False, frozen_until=None, can_unfreeze=False, task_link='',
    )
