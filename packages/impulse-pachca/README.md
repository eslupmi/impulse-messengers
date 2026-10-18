# impulse-pachca

Pachca messenger provider for IMPulse

## Pachca bot

1. Create a bot in **Integrations > Bots** and add it to every incident channel.
2. On the **API** tab issue a token with the scopes `messages:create`, `messages:update`, `threads:create`,
   `users:read`, `group_tags:read` and `bot_self:webhook:write`.
3. On the **Outgoing webhook** tab enable the **Button clicks** event and copy the **Signing secret**.
   IMPulse registers `<impulse_address>/app` as the webhook URL on startup.
4. Optional: enable **OAuth** for IMPulse UI login and add the `AUTH_REDIRECT_URL` value as a redirect URI.

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
```

## Build

```sh
uv build
```

## Limitations and choices you may want to revisit:
- Pachca doesn't document a URL for a user's profile page, so the user profile link in the IMPulse UI is empty.
- The plugin doesn't store thread IDs. Each notification first calls POST `/messages/{id}/thread`, which returns the existing thread if there is one, so every notification costs two requests.
- Registering the webhook URL at startup needs the bot_self:webhook:write scope, and IMPulse fails to start if the bot doesn't have it. To set the URL by hand instead, that call can be removed.
- To bundle it with the other plugins, `impulse-pachca` still needs a line in the core's `requirements.txt`.
