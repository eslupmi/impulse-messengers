import { IAppAccessors, IConfigurationExtend, IHttp, ILogger, IModify, IRead } from '@rocket.chat/apps-engine/definition/accessors';
import { ApiSecurity, ApiVisibility } from '@rocket.chat/apps-engine/definition/api';
import { App } from '@rocket.chat/apps-engine/definition/App';
import { IAppInfo } from '@rocket.chat/apps-engine/definition/metadata';
import { IJobContext } from '@rocket.chat/apps-engine/definition/scheduler';
import { ISetting, SettingType } from '@rocket.chat/apps-engine/definition/settings';
import { IUIKitInteractionHandler, IUIKitResponse, UIKitBlockInteractionContext } from '@rocket.chat/apps-engine/definition/uikit';

import { IncidentEndpoint } from './IncidentEndpoint';

const CLICK = 'deliver-click';

export class ImpulseRocketChatApp extends App implements IUIKitInteractionHandler {
    constructor(info: IAppInfo, logger: ILogger, accessors: IAppAccessors) {
        super(info, logger, accessors);
    }

    public async extendConfiguration(configuration: IConfigurationExtend): Promise<void> {
        const settings: ISetting[] = [
            setting('impulse_address', SettingType.STRING, 'IMPulse address', 'Base URL of the IMPulse server'),
            setting('webhook_token', SettingType.PASSWORD, 'IMPulse webhook token', 'Same value as ROCKETCHAT_WEBHOOK_TOKEN'),
            setting('app_token', SettingType.PASSWORD, 'IMPulse app token', 'Same value as ROCKETCHAT_APP_TOKEN'),
        ];
        for (const item of settings) {
            await configuration.settings.provideSetting(item);
        }
        await configuration.api.provideApi({
            visibility: ApiVisibility.PUBLIC,
            security: ApiSecurity.UNSECURE,
            endpoints: [new IncidentEndpoint(this)],
        });
        await configuration.scheduler.registerProcessors([{ id: CLICK, processor: this.deliverClick }]);
    }

    public async executeBlockActionHandler(context: UIKitBlockInteractionContext, read: IRead, _http: IHttp, _persistence: unknown, modify: IModify): Promise<IUIKitResponse> {
        const data = context.getInteractionData();
        const text = data.value || '';
        const address = String(await read.getEnvironmentReader().getSettings().getValueById('impulse_address') || '').replace(/\/$/, '');
        const token = await read.getEnvironmentReader().getSettings().getValueById('webhook_token');
        if (text.startsWith('impulse ') && address && token && data.room && data.user) {
            await modify.getScheduler().scheduleOnce({
                id: CLICK,
                when: '1 second',
                data: {
                    address: `${address}/app`,
                    token,
                    timestamp: new Date().toISOString(),
                    channel_id: data.room.id,
                    user_id: data.user.id,
                    text,
                },
            });
        }
        return context.getInteractionResponder().successResponse();
    }

    public async deliverClick(job: IJobContext, _read: IRead, _modify: IModify, http: IHttp): Promise<void> {
        await http.post(String(job.address), {
            headers: { 'Content-Type': 'application/json' },
            data: {
                token: job.token,
                timestamp: job.timestamp,
                channel_id: job.channel_id,
                user_id: job.user_id,
                text: job.text,
            },
        });
    }
}

function setting(id: string, type: SettingType, i18nLabel: string, i18nDescription: string): ISetting {
    return { id, type, packageValue: '', required: true, public: false, i18nLabel, i18nDescription };
}
