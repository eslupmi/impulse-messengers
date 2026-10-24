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
            data = await response.json()
            if not isinstance(data, dict):
                raise AuthenticationError('auth_failed', 'Invalid Pachca authentication response')
            return data
        finally:
            response.close()

    async def authenticate_callback(self, params, redirect_uri, http):
        if params.get('error'):
            raise AuthenticationError('provider_error')
        if params.get('iss') not in (None, 'https://api.pachca.com'):
            raise AuthenticationError('provider_error', 'Invalid Pachca authorization issuer')
        code = params.get('code')
        if not isinstance(code, str) or not code.strip():
            raise AuthenticationError('missing_code')
        data = await self._read(await http.post(self.token_url, data={
            'grant_type': 'authorization_code', 'client_id': self.client_id,
            'client_secret': self.client_secret, 'code': code, 'redirect_uri': redirect_uri,
        }))
        token = data.get('access_token')
        if not isinstance(token, str) or not token.strip():
            raise AuthenticationError('auth_failed', 'Pachca access token not found in response')
        data = await self._read(await http.get(self.token_info_url, headers={'Authorization': f'Bearer {token}'}))
        owner = data.get('data')
        user_id = owner.get('user_id') if isinstance(owner, dict) else None
        if type(user_id) is not int or user_id <= 0:
            raise AuthenticationError('auth_failed', 'Pachca user id not found in response')
        return AuthenticationUser(id=str(user_id))
