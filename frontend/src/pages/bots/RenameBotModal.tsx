// Phase 2-06: RenameBotModal — rename a bot profile.
// Current bot name shown read-only. new_name validated with BOT-08 regex.
// All errors go through extractErrorMessage (W5 harmonization).

import { ModalForm, ProFormText } from '@ant-design/pro-components';
import { App as AntdApp } from 'antd';
import { useRenameBot } from '@/hooks/useBots';
import { zhCN } from '@/i18n/zh-CN';
import { extractErrorMessage } from '@/utils/errors';
import type { BotOut } from '@/api/types';

type Props = {
  bot: BotOut;
  open: boolean;
  onClose: () => void;
};

// Mirror of backend BOT-08 regex: ^[a-z0-9][a-z0-9-]{2,31}$
const NAME_RE = /^[a-z0-9][a-z0-9-]{2,31}$/;

export default function RenameBotModal({ bot, open, onClose }: Props) {
  const { message } = AntdApp.useApp();
  const mutate = useRenameBot(bot.name);

  return (
    <ModalForm<{ new_name: string }>
      title={zhCN.bots.renameModal.title}
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      modalProps={{ destroyOnHidden: true, maskClosable: false }}
      submitter={{ searchConfig: { submitText: zhCN.bots.renameModal.submitButton } }}
      onFinish={async ({ new_name }) => {
        try {
          await mutate.mutateAsync({ new_name });
          message.success(zhCN.bots.renameModal.successMessage);
          onClose();
          return true;
        } catch (e) {
          message.error(extractErrorMessage(e)); // W5
          return false;
        }
      }}
    >
      <div style={{ marginBottom: 16 }}>
        {zhCN.bots.renameModal.currentLabel}：<code>{bot.name}</code>
      </div>
      <ProFormText
        name="new_name"
        label={zhCN.bots.renameModal.newNameLabel}
        rules={[
          { required: true },
          {
            validator: (_: unknown, value: string) => {
              if (!value) return Promise.resolve();
              if (value === 'default')
                return Promise.reject(new Error("不能为 'default'"));
              if (!NAME_RE.test(value))
                return Promise.reject(new Error(zhCN.bots.createModal.nameRule));
              return Promise.resolve();
            },
          },
        ]}
        placeholder="new-name"
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        fieldProps={{ 'data-testid': 'rename-bot-new-name' } as any}
      />
    </ModalForm>
  );
}
