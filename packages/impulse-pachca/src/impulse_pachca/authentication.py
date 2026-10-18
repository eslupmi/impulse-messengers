"""Pachca OAuth wire protocol; the core owns HTTP sessions and login policy."""
from urllib.parse import urlencode

from impulse_messenger_api import AuthenticationError, AuthenticationUser

from . import API_BASE, APP_URL


class PachcaAuthentication:
    name = 'pachca'
    authorize_url = f'{APP_URL}/apps/authorize'
    token_url = f'{API_BASE}/oauth/token'
    token_info_url = f'{API_BASE}/oauth/token/info'

    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret

    def build_authorization_url(self, state, redirect_uri):
        return self.authorize_url + '?' + urlencode({
            'response_type': 'code', 'client_id': self.client_id, 'redirect_uri': redirect_uri, 'state': state,
        })

    @staticmethod
    async def _read(response):
        try:
            if response.status != 200:
                raise AuthenticationError('auth_failed', 'Pachca authentication request failed')
            return await response.json()
        finally:
            response.close()

    async def authenticate_callback(self, params, redirect_uri, http):
        if params.get('error'):
            raise AuthenticationError('provider_error')
        code = params.get('code')
        if not code:
            raise AuthenticationError('missing_code')
        data = await self._read(await http.post(self.token_url, data={
            'grant_type': 'authorization_code', 'client_id': self.client_id,
            'client_secret': self.client_secret, 'code': code, 'redirect_uri': redirect_uri,
        }))
        token = data.get('access_token')
        if not token:
            raise AuthenticationError('auth_failed', 'Pachca access token not found in response')
        data = await self._read(await http.get(self.token_info_url, headers={'Authorization': f'Bearer {token}'}))
        return AuthenticationUser(id=str(data['data']['user_id']))
