"""Rocket.Chat third-party OAuth login; the core owns state and sessions."""
from urllib.parse import urlencode

from impulse_messenger_api import AuthenticationError, AuthenticationUser


class RocketChatAuthentication:
    name = 'rocketchat'

    def __init__(self, base_url, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        base_url = base_url.rstrip('/')
        self.authorize_url = f'{base_url}/oauth/authorize'
        self.token_url = f'{base_url}/oauth/token'
        self.user_url = f'{base_url}/api/v1/me'

    def build_authorization_url(self, state, redirect_uri):
        return self.authorize_url + '?' + urlencode({
            'response_type': 'code', 'client_id': self.client_id, 'redirect_uri': redirect_uri, 'state': state,
        })

    @staticmethod
    async def _read(response):
        try:
            if response.status != 200:
                raise AuthenticationError('auth_failed', 'Rocket.Chat authentication request failed')
            data = await response.json()
            if not isinstance(data, dict):
                raise AuthenticationError('auth_failed', 'Rocket.Chat authentication response is invalid')
            return data
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
        if not isinstance(token, str) or not token:
            raise AuthenticationError('auth_failed', 'Rocket.Chat access token not found in response')
        data = await self._read(await http.get(self.user_url, headers={'Authorization': f'Bearer {token}'}))
        user_id = data.get('_id')
        if not isinstance(user_id, str) or not user_id:
            raise AuthenticationError('auth_failed', 'Rocket.Chat user ID not found in response')
        return AuthenticationUser(user_id, username=data.get('username'), full_name=data.get('name'),
                                  email=data.get('email'))
