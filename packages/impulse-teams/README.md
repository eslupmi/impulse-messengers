# impulse-teams

Microsoft Teams messenger provider for IMPulse

## IMPulse functionality

| IMPulse feature | Teams support |
| --- | --- |
| Incident creation and status updates | Bot-posted Adaptive Card in a standard channel |
| Incident notification thread | Replies in the original channel conversation |
| Take It / Release | Verified card submission and original-card update |
| Freeze / unfreeze | Native expanding freeze choices and verified submission |
| Task creation | Existing IMPulse task action and task link |
| User notifications | Entra user mentions with Adaptive Card mention entities |
| IMPulse `user_groups` | Individual mentions of each configured member |
| Native messenger groups | Unsupported; nonempty `groups` is rejected |
| Users and assignment | Paginated team roster, correlated by Entra object ID |
| IMPulse UI login | Optional tenant-scoped OAuth and Graph `/me` |

These map to the existing `impulse_messenger_api` version 1 contracts. Microsoft documents
[proactive channel conversations](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages),
[card actions](https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/cards-actions)
and [user mentions](https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/cards-format).

## Teams bot

1. Create a **single-tenant** Microsoft Entra app registration and client secret, then an Azure Bot resource
   using that app ID and tenant. Enable its **Microsoft Teams** channel.
2. Set the bot's messaging endpoint to `<impulse_address>/app`. Create a Teams app manifest with the bot's
   app ID and the `team` scope, and install that app in the team containing every incident channel.
   This provider uses Bot Connector messaging, rather than Incoming Webhooks or Graph message sending.
3. From a Teams activity delivered to that endpoint, record `serviceUrl`, `channelData.team.id`,
   `channelData.tenant.id` and `channelData.channel.id`. A team installation or a message to the bot
   produces activities; use the Azure Bot diagnostic tooling or a temporary diagnostic receiver during setup.
   Configure the exact `serviceUrl` from the activity, including its regional path, before starting IMPulse.
   The tenant ID is a GUID; the Bot Framework team ID and channel ID are opaque `19:...` strings.
4. Configure users with their **Microsoft Entra object IDs**, not the bot-specific `29:...` user IDs.
   The bot resolves their names and email through the team's paginated Connector roster.
5. Optional: for IMPulse UI login, configure a Web redirect URI equal to `AUTH_REDIRECT_URL` on an app
   registration in the same tenant, with delegated Microsoft Graph **User.Read** permission.
   Set `AUTH_CLIENT_ID`, `AUTH_CLIENT_SECRET` and `AUTH_REDIRECT_URL` as described by IMPulse.
   This login uses `/me` to obtain the same Entra object ID as bot interactions.

## Configuration

```yaml
messenger:
  type: teams
  impulse_address: https://<your_impulse_domain>
  tenant_id: 11111111-1111-1111-1111-111111111111
  team_id: 19:<bot_framework_team_id>@thread.tacv2
  service_url: https://smba.trafficmanager.net/emea
  admin_users: ["Dmitry"]
  channels:
    incidents_default: {id: "19:<channel_id>@thread.tacv2"}
  users:
    Dmitry: {id: "22222222-2222-2222-2222-222222222222"}
  user_groups:
    devops: {users: ["Dmitry"]}
```

Environment:

```sh
TEAMS_APP_ID=<azure_bot_app_id>
TEAMS_APP_SECRET=<azure_bot_app_client_secret>

# Optional IMPulse UI login:
AUTH_CLIENT_ID=<entra_oauth_client_id>
AUTH_CLIENT_SECRET=<entra_oauth_client_secret>
AUTH_REDIRECT_URL=https://<your_impulse_domain>/auth/callback
```

The `service_url` above is an example. Use the actual activity's value; the provider rejects callbacks
from another service URL, tenant, team or unconfigured channel. It verifies Connector JWT signatures,
issuer, audience, expiration, not-before time, the signed service URL and the signing key's Teams endorsement.
Signing keys are refreshed at least daily, with a five-minute minimum between unknown-key refreshes.

## Build

From the messenger repository root:

```sh
uv build --package impulse-teams
```

Install into the same Python environment as IMPulse, then restart it:

```sh
python -m pip install ./packages/impulse-teams
```

## Limitations and choices you may want to revisit:

- Supported: creating incident cards, updating them after status/assignment/freeze/task changes, thread replies,
  user mentions, IMPulse `user_groups`, Take It/Release, freeze/unfreeze choices, task creation and optional UI login.
  Buttons use documented Adaptive Card `Action.Submit`; freeze choices expand with `Action.ShowCard`.
- This package targets standard channels in one team and one tenant in the Microsoft public cloud.
  Personal chats, group chats, private/shared channels and sovereign clouds have not been implemented or qualified.
- Native Teams tags/groups are not implemented. Nonempty `messenger.groups` is rejected;
  use IMPulse `user_groups` to notify several users. Adding native tags requires separate Graph permissions.
- Teams has no documented user-profile URL for this flow, so the user profile link in the IMPulse UI is empty.
  The Connector roster does not expose a timezone; IMPulse's configured/default timezone applies.
- Connector conversation IDs are opaque. The provider stores the returned conversation and activity IDs together
  in IMPulse's existing thread-ID string; no core schema or persistence contract is added.
- Each user refresh walks the team's paginated roster until the user is found. Large teams can require multiple
  requests per user. Requests use IMPulse's existing rate-limited transport and bot tokens are cached until expiry.
- If Microsoft changes a regional service URL, update `service_url` using a verified activity and restart IMPulse.
  Service URLs supplied by callbacks never become arbitrary outbound request destinations.
- Bot card/message size and throttling limits still apply; oversized messages are not split automatically.
- This package must be installed in the IMPulse environment to enable `type: teams`.
  Temporary development checks exercised real JWT verification and mocked REST calls; a tenant installation and live Teams workflow
  still need deployment verification.

Official references:

- [Proactive messages and installation requirements](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages)
- [Bot Connector REST endpoints](https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-api-reference)
- [Connector authentication and JWT validation](https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-authentication)
- [Teams roster and user context](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/get-teams-context)
- [Adaptive Card actions](https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/cards-actions)
- [User mentions in cards](https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/cards-format)
- [Entra OAuth authorization code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
- [Microsoft Graph /me](https://learn.microsoft.com/en-us/graph/api/user-get)
