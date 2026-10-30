# impulse-rocketchat

Rocket.Chat messenger provider for IMPulse

Incident buttons target [Rocket.Chat **8.9.0 LTS](https://github.com/RocketChat/Rocket.Chat/releases/tag/8.9.0)**
and the Apps-Engine `1.67.0` API shipped with that release. Deployment behavior still requires verification.

## IMPulse functionality


| IMPulse function                             | Rocket.Chat support                                                                                                                                                                                                            |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Incident creation and status/body updates    | The installed IMPulse app writes the message. The header stays in `msg`, the body and freeze status live in a status-colored attachment, and the buttons are UI Kit blocks. IMPulse stores the message ID returned by the app. |
| Incident notifications and escalation chains | Replies with `tmid` through `chat.postMessage` and `parseUrls: false`, so thread replies do not generate link previews; native `@username` mentions.                                                                           |
| Configured user groups                       | IMPulse expands `user_groups` into individual mentions.                                                                                                                                                                        |
| Messenger groups                             | Rocket.Chat teams, read with `teams.info` and mentioned as `@teamname`; team mentions must be enabled in the workspace.                                                                                                        |
| Assignment and release                       | **Take It** or **Release** is a UI Kit button. The app posts the click to IMPulse, which chooses the action from current incident state.                                                                                       |
| Freeze and unfreeze                          | **Freeze** replaces the message's buttons with **Tomorrow**, **Next Monday**, **In Month**, **In 6 months** and **Back**. Selection restores the normal controls; the frozen incident's button clears a manual freeze.         |
| Task creation                                | An optional **Create Task** button calls the existing IMPulse task integration.                                                                                                                                                |
| IMPulse UI login                             | Rocket.Chat third-party OAuth authorization code flow and authenticated `me` lookup.                                                                                                                                           |
| Incident links                               | Rocket.Chat universal room deep links with the root message ID.                                                                                                                                                                |
| User profile link and timezone               | No profile link; the API's numeric UTC offset is not an IANA timezone, so IMPulse's configured timezone applies.                                                                                                               |


Thread replies, user lookup and team lookup use the official [messaging REST API](https://developer.rocket.chat/apidocs/messaging-api),
[user lookup](https://developer.rocket.chat/apidocs/get-users-info), and
[team lookup](https://developer.rocket.chat/apidocs/get-team-info).
Incident buttons use the [Apps-Engine UI Kit](https://developer.rocket.chat/docs/building-blocks).
UI login uses [third-party OAuth](https://docs.rocket.chat/docs/third-party-login).
Team mentions follow Rocket.Chat's
[mention implementation](https://github.com/RocketChat/Rocket.Chat/blob/1826a86264c14ebb8b6130a5b89ed8fc37cb2ef9/apps/meteor/server/services/messages/hooks/BeforeSaveMentions.ts).

## Rocket.Chat app

Colored buttons are a private Apps-Engine app. Rocket.Chat delivers a UI Kit click only to the app that sent the message, so incident messages are created and edited by that app. The bot user below is still required for thread replies and for `users.info` / `teams.info`.

1. Build the zip from `packages/impulse-rocketchat/app`:
  ```sh
   npm install
   npx rc-apps package
  ```
   The package is `dist/impulse_0.1.0.zip` (the slug and version follow `app.json`). The app targets Apps-Engine `^1.67.0`, the engine shipped with Rocket.Chat 8.9.
2. In **Marketplace > Explore > Private Apps**, upload that zip and accept the requested permissions.
  Private upload needs a self-hosted workspace or Dedicated cloud on the Starter plan or higher. On an older server, also enable **Manage > General > Apps > Enable development mode**.
3. Open the installed app's **Settings** and set **IMPulse address** to `https://<your_impulse_domain>` with no path, **IMPulse webhook token** to the same string as `ROCKETCHAT_WEBHOOK_TOKEN`, and **IMPulse app token** to the same string as `ROCKETCHAT_APP_TOKEN`.
  The app calls `<IMPulse address>/app` about a second after a click. IMPulse rejects that call when it is older than five minutes or the webhook token does not match.
4. Copy the app ID from the app details. It is `b7e4c1a0-9f28-4d6e-a3c5-1e8b0d7f6a42` unless `app.json` was changed. Put it in `messenger.app_id`.
5. Add both users to every configured incident room and private team: the bot from the next section, and the user Rocket.Chat created for this app. The app user must be able to post and edit its own messages for the life of an incident. Enable message editing and set **Block Message Editing After** to `0`.



## Rocket.Chat bot

1. Create a dedicated bot user and add it to every configured incident room and private team.
2. Issue a personal access token for that user and record its user ID. The bot needs permission to send thread replies
  and to read configured users and teams. Access to a private team's information requires team
   membership or `view-all-teams`; membership avoids granting workspace-wide team access.
3. Ensure the bot's REST rate limits accommodate one request per second. Rocket.Chat's
  `api-bypass-rate-limit` [permission](https://developer.rocket.chat/apidocs) can exempt a dedicated integration role;
   otherwise configure the workspace REST limits accordingly. Workspace defaults vary by endpoint.
4. Optional: in **Manage > Workspace > Third-party login**, register an OAuth application for IMPulse UI login.
  Use the `AUTH_REDIRECT_URL` value as its redirect URI and configure the application's client ID and secret through
   IMPulse's existing `AUTH_CLIENT_ID` and `AUTH_CLIENT_SECRET` variables. Messenger buttons work without UI login.
   This OAuth client is not the private app.



## Configuration

```yaml
messenger:
  type: rocketchat
  address: https://<your_rocketchat_domain>
  app_id: b7e4c1a0-9f28-4d6e-a3c5-1e8b0d7f6a42
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
ROCKETCHAT_WEBHOOK_TOKEN=<shared_callback_token>
ROCKETCHAT_APP_TOKEN=<shared_app_token>

# Optional IMPulse UI login:
AUTH_CLIENT_ID=<rocketchat_oauth_client_id>
AUTH_CLIENT_SECRET=<rocketchat_oauth_client_secret>
AUTH_REDIRECT_URL=https://<your_impulse_domain>/auth/callback
```

Install `impulse-rocketchat` into the same environment as IMPulse; it registers through `impulse.messengers`.
After startup, create a test incident, use **Take It** or **Release**, and confirm a notification appears in the same thread.
Click **Freeze** and confirm the same message shows the four duration choices and **Back**.
Check that **Back** restores the normal buttons without freezing, then choose a duration.
Use the frozen incident's button to clear the manual freeze, and check maintenance or inhibition freezes remain intact.

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

- Incident create and update go to `POST /api/apps/public/<app_id>/incident`. The app sends the message as its own user, then writes the UI Kit buttons with that message ID. Messages created earlier by the bot user are left unchanged; the app returns an error instead of rewriting them.
- Turning the app off leaves the buttons on existing messages, and clicks do nothing until the app is enabled again. Changing `id` in `app.json` does the same to every button already sent.
- A click does not post `impulse <id> <action>` into the room. The app schedules that payload to `<impulse_address>/app` and returns immediately, so the button does not wait on the incident update. Typing the same command in the room does nothing. Duration choices are accepted only while that button is still on the message. IMPulse checks this with `chat.getMessage` and the button `value` in `blocks`. The read and the core action are not atomic.
- Thread replies set `parseUrls` to `false` on `chat.postMessage`, which skips URL previews. The incident body lives in
the colored attachment, and the buttons live in one `actions` block. The assignee name in the body links to `<workspace>/direct/<username>` and does not notify. Other `@mentions` in that body are display-only and do not notify. Buttons are rebuilt from
IMPulse's current presentation, including maintenance/inhibition and manual-unfreeze state, and removed when the
incident closes. IMPulse continues to own incident lookup, durations and business actions.
- Team mentions depend on Rocket.Chat's workspace features and settings. Use IMPulse `user_groups` when explicit individual
mentions are preferred. An HTTP success response confirms acceptance, not delivery of a push notification to a device.
- Rate limiting is intentionally conservative at one request per second. Bursts queue in IMPulse; callbacks share that
transport and can be delayed. The plugin does not create rooms, join teams, or alter workspace permissions.
- The user profile link is empty. User timezones fall back to IMPulse configuration; numeric `utcOffset` cannot identify
a region or its daylight saving rules.
- Universal incident links use the documented [room deep link](https://developer.rocket.chat/docs/deep-linking) and the `mid`
parameter used by Rocket.Chat's own [notification links](https://github.com/RocketChat/Rocket.Chat/blob/1826a86264c14ebb8b6130a5b89ed8fc37cb2ef9/apps/meteor/server/lib/notifications/message/email.js).
Browser/mobile navigation, attachment rendering, workspace permissions, OAuth consent, and real notification delivery still
need verification against your Rocket.Chat deployment. Temporary transport checks do not establish live workspace compatibility.

