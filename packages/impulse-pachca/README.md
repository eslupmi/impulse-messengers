# impulse-pachca

Pachca messenger provider for IMPulse

## IMPulse functionality

| Functionality | Support |
| --- | --- |
| Create and update incident messages | Supported, with Markdown formatting. |
| Incident notifications and escalation chains | Supported as replies in the incident's thread. |
| Take It, Release, Freeze/Unfreeze and task actions | Supported through signed button callbacks; IMPulse owns the actions. |
| Users, configured user groups and admins | Supported with user IDs and `@nickname` mentions. |
| Messenger groups | Supported through Pachca group tags; use tag names without spaces for mentions. |
| Incident links | Supported; user profile links are unavailable. |
| IMPulse UI login | Supported through a confidential Pachca OAuth client, subject to workspace policy. |

API references: [messages](https://dev.pachca.com/api/messages/create),
[message updates](https://dev.pachca.com/api/messages/update),
[threads](https://dev.pachca.com/guides/threads),
[buttons](https://dev.pachca.com/guides/buttons),
[webhook verification](https://dev.pachca.com/guides/webhook/handler),
[users](https://dev.pachca.com/api/users/get),
[tags](https://dev.pachca.com/guides/tags) and
[OAuth](https://dev.pachca.com/guides/oauth/flow).

## Pachca bot

1. Create a bot in **Integrations > Bots** and add it to every incident channel.
2. On the **API** tab issue a token with the scopes `messages:create`, `messages:update`, `threads:create`,
   `users:read`, `group_tags:read` and `bot_self:webhook:write`.
3. On the **Outgoing webhook** tab enable the **Button clicks** event and copy the **Signing secret**.
   IMPulse registers `<impulse_address>/app` as the webhook URL on startup.
4. Optional: enable **OAuth** for IMPulse UI login, select the server application with a client secret,
   and register the exact public callback URL, such as `https://<your_impulse_domain>/auth/callback`.
   Set the resulting credentials in `AUTH_CLIENT_ID` and `AUTH_CLIENT_SECRET`, and the same callback URL
   in `AUTH_REDIRECT_URL`. Include IMPulse's HTTP prefix in the URL when one is configured.
   OAuth requires at least one permission in the bot's requested access settings; token-info lookup itself
   requires no additional permission. Request only the permissions your bot needs.
   By default Pachca allows only workspace administrators to authorize bots. To allow configured
   employees to log in, the workspace policy for creating personal tokens must allow all employees;
   guests cannot authorize bots. See [OAuth setup](https://dev.pachca.com/guides/oauth/setup) and
   [consent restrictions](https://dev.pachca.com/guides/oauth/consent).

## Configuration

```yaml
messenger:
  type: pachca
  impulse_address: https://<your_impulse_domain>
  admin_users: ["Dmitry"]
  channels:
    incidents_default: {id: 334}
  users:
    Dmitry: {id: 12}
  groups:
    devops: {id: 9111}
```

Environment:

```sh
PACHCA_BOT_TOKEN=<bot_token>
PACHCA_SIGNING_SECRET=<outgoing_webhook_signing_secret>

# Optional IMPulse UI login:
AUTH_CLIENT_ID=<oauth_client_id>
AUTH_CLIENT_SECRET=<oauth_client_secret>
AUTH_REDIRECT_URL=https://<your_impulse_domain>/auth/callback
```

## Build

From the messenger repository root:

```sh
uv build --package impulse-pachca
```

Install into the same Python environment as IMPulse, then restart IMPulse so it discovers the provider:

```sh
python -m pip install ./packages/impulse-pachca
```

## Limitations and choices you may want to revisit:
- Pachca doesn't document a URL for a user's profile page, so the user profile link in the IMPulse UI is empty.
- The plugin doesn't store thread IDs. Each notification first calls POST `/messages/{id}/thread`, which returns the existing thread if there is one, so every notification costs two requests.
- Registering the webhook URL at startup needs the bot_self:webhook:write scope, and IMPulse fails to start if the bot doesn't have it.
- Pachca documents group mentions as `@` followed by a tag name without spaces; the plugin does not guess an encoding for tags containing spaces.
- Pachca enforces per-recipient and global delivery rate limits, plus a rolling daily message quota per chat. The provider uses IMPulse's existing queue and reports API failures; it adds no separate retry service. See [API limits](https://dev.pachca.com/api/limits).
- OAuth state, login sessions and the configured-user allowlist remain owned by IMPulse. The plugin uses the documented confidential-client flow; PKCE is recommended by Pachca but requires state beyond the existing authentication contract.
- Development validation used temporary checks with mocked HTTP responses and signed callbacks. Live Pachca delivery, button rendering and OAuth login still need a bot and workspace credentials.
