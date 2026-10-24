# impulse-rocketchat

Rocket.Chat messenger provider for IMPulse

## IMPulse functionality

| IMPulse function | Rocket.Chat support |
| --- | --- |
| Incident creation and status/body updates | REST `chat.sendMessage` and `chat.update`; IMPulse stores the root message ID. |
| Incident notifications and escalation chains | Replies with `tmid` through `chat.postMessage`; native `@username` mentions. |
| Configured user groups | IMPulse expands `user_groups` into individual mentions. |
| Messenger groups | Rocket.Chat teams, read with `teams.info` and mentioned as `@teamname`; team mentions must be enabled in the workspace. |
| Assignment and release | A static **Take It / Release** attachment button sends an outgoing integration event; IMPulse chooses the action from current incident state. |
| Freeze and unfreeze | **Freeze in IMPulse** opens the existing IMPulse UI; sign in and select the incident to set its freeze duration. The native **Unfreeze** button uses IMPulse's existing manual-unfreeze checks. |
| Task creation | An optional static **Create Task** button calls the existing IMPulse task integration. |
| IMPulse UI login | Rocket.Chat third-party OAuth authorization code flow and authenticated `me` lookup. |
| Incident links | Rocket.Chat universal room deep links with the root message ID. |
| User profile link and timezone | No profile link; the API's numeric UTC offset is not an IANA timezone, so IMPulse's configured timezone applies. |

The implementation follows the official [messaging REST API](https://developer.rocket.chat/apidocs/messaging-api),
[send-message API](https://developer.rocket.chat/apidocs/send-message),
[update-message API](https://developer.rocket.chat/apidocs/update-message),
[user lookup](https://developer.rocket.chat/apidocs/get-users-info),
[team lookup](https://developer.rocket.chat/apidocs/get-team-info),
[outgoing integration setup](https://docs.rocket.chat/docs/integrations), and
[third-party OAuth setup](https://docs.rocket.chat/docs/third-party-login).
The native attachment button behavior is confirmed by Rocket.Chat's own
[button handler](https://github.com/RocketChat/Rocket.Chat/blob/1826a86264c14ebb8b6130a5b89ed8fc37cb2ef9/apps/meteor/client/components/message/content/attachments/default/hooks/usePerformActionMutation.ts),
[outgoing payload builder](https://github.com/RocketChat/Rocket.Chat/blob/1826a86264c14ebb8b6130a5b89ed8fc37cb2ef9/apps/meteor/server/lib/integrations/lib/triggerHandler.ts), and
[team mention implementation](https://github.com/RocketChat/Rocket.Chat/blob/1826a86264c14ebb8b6130a5b89ed8fc37cb2ef9/apps/meteor/server/services/messages/hooks/BeforeSaveMentions.ts).

## Rocket.Chat bot

1. Create a dedicated bot user and add it to every configured incident room and private team.
2. Issue a personal access token for that user and record its user ID. The bot needs permission to send messages,
   read configured users/teams, and edit its own messages. Enable message editing and set **Block Message Editing After**
   to `0` so incidents can be updated for their entire lifetime. Access to a private team's information requires team
   membership or `view-all-teams`; membership avoids granting workspace-wide team access.
3. In **Manage > Workspace > Integrations**, create an **Outgoing** integration with event **Message Sent**.
   Enable it, enter the configured incident rooms as the channels, set **Trigger Words** to `impulse`,
   set **URLs** to `<impulse_address>/app`, and set **Post as** to the bot username.
   Set a strong **Token** and copy it to `ROCKETCHAT_WEBHOOK_TOKEN`. Leave scripting disabled.
4. Disable **Run On Edits**, **Trigger Word Anywhere**, and automatic retries for this integration.
   Button commands change incident state, so edited or replayed events must not toggle it again.
   Use HTTPS for both Rocket.Chat and the IMPulse callback URL. The integration authenticates callbacks with its
   shared token; Rocket.Chat does not sign this native payload. IMPulse rejects messages older than five minutes.
5. Ensure the bot's REST rate limits accommodate one request per second. Rocket.Chat's
   [`api-bypass-rate-limit` permission](https://developer.rocket.chat/apidocs) can exempt a dedicated integration role;
   otherwise configure the workspace REST limits accordingly. Workspace defaults vary by endpoint.
6. Optional: in **Manage > Workspace > Third-party login**, register an OAuth application for IMPulse UI login.
   Use the `AUTH_REDIRECT_URL` value as its redirect URI and configure the application's client ID and secret through
   IMPulse's existing `AUTH_CLIENT_ID` and `AUTH_CLIENT_SECRET` variables. Configure UI login to initiate freezes
   through **Freeze in IMPulse**.

## Configuration

```yaml
messenger:
  type: rocketchat
  address: https://<your_rocketchat_domain>
  impulse_address: https://<your_impulse_domain>
  admin_users: ["Dmitry"]
  channels:
    incidents_default: {id: "<room_id>"}
  users:
    Dmitry: {id: "<user_id>"}
  groups:
    devops: {id: "<team_id>"}
```

Use room IDs rather than `#channel` names so callback IDs match IMPulse's stored references.
Messenger `groups` are Rocket.Chat teams; Rocket.Chat's REST `groups.*` endpoints describe private rooms instead.

Environment:

```sh
ROCKETCHAT_AUTH_TOKEN=<bot_personal_access_token>
ROCKETCHAT_USER_ID=<bot_user_id>
ROCKETCHAT_WEBHOOK_TOKEN=<outgoing_integration_token>

# IMPulse UI login, required to initiate freezes through the UI:
AUTH_CLIENT_ID=<rocketchat_oauth_client_id>
AUTH_CLIENT_SECRET=<rocketchat_oauth_client_secret>
AUTH_REDIRECT_URL=https://<your_impulse_domain>/auth/callback
```

Install `impulse-rocketchat` into the same environment as IMPulse; it registers through `impulse.messengers`.
After startup, create a test incident, use **Take It / Release**, and confirm a notification appears in the same thread.
Use **Freeze in IMPulse** to open the UI, sign in, find the test incident, and freeze it.
Confirm **Unfreeze** clears that manual freeze and leaves maintenance or inhibition freezes intact.

## Build

From the messenger repository root:

```sh
uv build --package impulse-rocketchat
```

Install into the same Python environment as IMPulse, then restart it:

```sh
python -m pip install ./packages/impulse-rocketchat
```

## Limitations and choices you may want to revisit:

- Rocket.Chat REST updates change message text, not attachment buttons. Buttons keep their initial labels and remain
  visible when an incident closes; IMPulse's active incident lookup and freeze rules determine whether an action applies.
  A task button appears only when task creation is configured when the incident is created. Dynamic buttons would require
  a separately installed Rocket.Chat Apps-Engine integration.
- Native duration buttons would remain active during maintenance and could overwrite an automatic time freeze through
  the current IMPulse callback contract. They are deliberately unavailable. **Freeze in IMPulse** opens the dashboard,
  where the existing UI enforces current incident state; UI login is required. **Unfreeze** remains a native action
  and does nothing when IMPulse disallows manual unfreezing.
- Clicking a button sends a visible message such as `impulse <incident_message_id> chain` under the clicking user.
  Typing the equivalent command has the same effect. The outgoing event supplies the actor and room; IMPulse owns incident lookup
  and business rules. Keep automatic webhook retries disabled because replaying a toggle can reverse a previous action.
- Team mentions depend on Rocket.Chat's workspace features and settings. Use IMPulse `user_groups` when explicit individual
  mentions are preferred. An HTTP success response confirms acceptance, not delivery of a push notification to a device.
- Rate limiting is intentionally conservative at one request per second. Bursts queue in IMPulse; callbacks share that
  transport and can be delayed. The plugin does not create rooms, join teams, register outgoing integrations, or alter workspace permissions.
- The user profile link is empty. User timezones fall back to IMPulse configuration; numeric `utcOffset` cannot identify
  a region or its daylight saving rules.
- Universal incident links use the documented [room deep link](https://developer.rocket.chat/docs/deep-linking) and the `mid`
  parameter used by Rocket.Chat's own [notification links](https://github.com/RocketChat/Rocket.Chat/blob/1826a86264c14ebb8b6130a5b89ed8fc37cb2ef9/apps/meteor/server/lib/notifications/message/email.js).
  Browser/mobile navigation, attachment rendering, workspace permissions, OAuth consent, and real notification delivery still
  need verification against your Rocket.Chat deployment. Local transport tests do not establish live workspace compatibility.
