"""Entra OAuth authorization-code login; core owns state and sessions."""
from urllib.parse import urlencode

from impulse_messenger_api import AuthenticationError, AuthenticationUser


class TeamsAuthentication:
    name = 'teams'

    def __init__(self, client_id, client_secret, tenant_id):
        self.client_id = client_id
        self.client_secret = client_secret
        self.authority = f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0'

    def build_authorization_url(self, state, redirect_uri):
        return self.authority + '/authorize?' + urlencode({
            'client_id': self.client_id, 'response_type': 'code', 'response_mode': 'query',
            'redirect_uri': redirect_uri, 'scope': 'https://graph.microsoft.com/User.Read', 'state': state,
        })

    @staticmethod
    async def _read(response):
        try:
            if response.status != 200:
                raise AuthenticationError('auth_failed', 'Teams authentication request failed')
            data = await response.json()
            if not isinstance(data, dict):
                raise AuthenticationError('auth_failed', 'Teams authentication response is invalid')
            return data
        finally:
            response.close()

    async def authenticate_callback(self, params, redirect_uri, http):
        if params.get('error'):
            raise AuthenticationError('provider_error')
        if not params.get('code'):
            raise AuthenticationError('missing_code')
        data = await self._read(await http.post(self.authority + '/token', data={
            'grant_type': 'authorization_code', 'client_id': self.client_id,
            'client_secret': self.client_secret, 'code': params['code'], 'redirect_uri': redirect_uri,
            'scope': 'https://graph.microsoft.com/User.Read',
        }, allow_redirects=False))
        token = data.get('access_token')
        if not isinstance(token, str) or not token:
            raise AuthenticationError('auth_failed', 'Teams access token not found in response')
        data = await self._read(await http.get('https://graph.microsoft.com/v1.0/me',
                                             headers={'Authorization': f'Bearer {token}'}, allow_redirects=False))
        if not isinstance(data.get('id'), str) or not data['id']:
            raise AuthenticationError('auth_failed', 'Teams user ID not found in response')
        return AuthenticationUser(id=data['id'], username=data.get('userPrincipalName'),
                                  full_name=data.get('displayName'), email=data.get('mail'))
