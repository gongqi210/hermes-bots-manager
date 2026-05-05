// Phase 2-06: DeleteBotModal — type-name-to-confirm delete modal (BOT-07).
// User must type the exact bot name to enable the submit button.
// Shows a warning alert and red submit button.
// All errors go through extractErrorMessage (W5 harmonization).

import { ModalForm, ProFormText } from '@ant-design/pro-components';
import { Alert, App as AntdApp } from 'antd';
import { useDeleteBot } from '@/hooks/useBots';
import { zhCN } from '@/i18n/zh-CN';
import { extractErrorMessage } from '@/utils/errors';
import type { BotOut } from '@/api/types';

type Props = { bot: BotOut; open: boolean; onClose: () => void };

export default function DeleteBotModal({ bot, open, onClose }: Props) {
  const { message } = AntdApp.useApp();
  const mutate = useDeleteBot(bot.name);
  return (
    <ModalForm
      title={zhCN.bots.deleteModal.title}
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      modalProps={{ destroyOnHidden: true }}
      submitter={{
        searchConfig: { submitText: zhCN.bots.deleteModal.submitButton },
        submitButtonProps: { danger: true },
      }}
      onFinish={async ({ confirm_name }: { confirm_name: string }) => {
        try {
          await mutate.mutateAsync({ confirm_name });
          message.success(zhCN.bots.deleteModal.successMessage);
          onClose();
          return true;
        } catch (e) {
          message.error(extractErrorMessage(e)); // W5
          return false;
        }
      }}
    >
      <Alert type="warning" message={zhCN.bots.deleteModal.warning} style={{ marginBottom: 16 }} />
      <div style={{ marginBottom: 8 }}>
        Bot 名称：<code>{bot.name}</code>
      </div>
      <ProFormText
        name="confirm_name"
        label={zhCN.bots.deleteModal.confirmLabel}
        placeholder={zhCN.bots.deleteModal.confirmPlaceholder}
        rules={[
          { required: true },
          {
            validator: (_: unknown, v: string) =>
              v === bot.name
                ? Promise.resolve()
                : Promise.reject(new Error(zhCN.bots.deleteModal.nameMismatch)),
          },
        ]}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        fieldProps={{ 'data-testid': 'delete-bot-confirm-input' } as any}
      />
    </ModalForm>
  );
}
