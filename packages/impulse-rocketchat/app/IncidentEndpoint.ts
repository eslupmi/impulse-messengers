import { HttpStatusCode, IModify, IRead } from '@rocket.chat/apps-engine/definition/accessors';
import { ApiEndpoint, IApiRequest, IApiResponse } from '@rocket.chat/apps-engine/definition/api';
import { IMessageAttachment } from '@rocket.chat/apps-engine/definition/messages';
import { IRoom } from '@rocket.chat/apps-engine/definition/rooms';
import { IBlock } from '@rocket.chat/apps-engine/definition/uikit';
import { IUser } from '@rocket.chat/apps-engine/definition/users';

const STYLES = ['primary', 'secondary', 'danger', 'success', 'warning'];

type Button = { text: string; action: string; style?: string };

export class IncidentEndpoint extends ApiEndpoint {
    public path = 'incident';

    public async post(request: IApiRequest, _endpoint: unknown, read: IRead, modify: IModify): Promise<IApiResponse> {
        const expected = String(await read.getEnvironmentReader().getSettings().getValueById('app_token') || '');
        const provided = request.headers['x-impulse-token'] || request.headers['X-Impulse-Token'] || '';
        if (!expected || provided !== expected) {
            return { status: HttpStatusCode.UNAUTHORIZED, content: { error: 'unauthorized' } };
        }
        const body = request.content || {};
        const room = await read.getRoomReader().getById(body.roomId);
        const appUser = await read.getUserReader().getAppUser();
        if (!room || !appUser) {
            return { status: HttpStatusCode.NOT_FOUND, content: { error: 'room' } };
        }
        const header = String(body.header || '');
        const text = String(body.text || '');
        const color = typeof body.color === 'string' ? body.color : '';
        const buttons: Button[] = Array.isArray(body.buttons) ? body.buttons : [];
        const id = typeof body.id === 'string' ? body.id : '';
        if (id) {
            const message = await read.getMessageReader().getById(id);
            if (!message) {
                return { status: HttpStatusCode.NOT_FOUND, content: { error: 'message' } };
            }
            if (message.sender.id !== appUser.id) {
                return { status: HttpStatusCode.FORBIDDEN, content: { error: 'owner' } };
            }
            await render(modify, appUser, room, id, header, color, text, buttons, this.app.getID());
            return this.success({ id });
        }
        const created = await modify.getCreator().finish(draft(modify, appUser, room, header, color, text));
        await render(modify, appUser, room, created, header, color, text, buttons, this.app.getID());
        return this.success({ id: created });
    }
}

function draft(modify: IModify, appUser: IUser, room: IRoom, header: string, color: string, text: string) {
    return modify.getCreator().startMessage().setSender(appUser).setRoom(room).setText(header).setAttachments([attachment(color, text)]);
}

async function render(modify: IModify, appUser: IUser, room: IRoom, id: string, header: string, color: string, text: string, buttons: Button[], appId: string): Promise<void> {
    const builder = await modify.getUpdater().message(id, appUser);
    builder.setRoom(room).setText(header).setAttachments([attachment(color, text)]).setBlocks(actionBlocks(appId, id, buttons)).setEditor(appUser);
    await modify.getUpdater().finish(builder);
}

function attachment(color: string, text: string): IMessageAttachment {
    return color ? { color, text } : { text };
}

function actionBlocks(appId: string, messageId: string, buttons: Button[]): IBlock[] {
    if (!buttons.length) {
        return [];
    }
    return [{
        type: 'actions',
        appId,
        blockId: 'incident_actions',
        elements: buttons.map((button, index) => ({
            type: 'button',
            actionId: button.action,
            appId,
            blockId: `incident_button_${index}`,
            text: { type: 'plain_text', text: button.text, emoji: true },
            ...(STYLES.includes(button.style || '') ? { style: button.style } : {}),
            value: `impulse ${messageId} ${button.action}`,
        })),
    } as IBlock];
}
