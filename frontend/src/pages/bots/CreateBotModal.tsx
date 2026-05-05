// 名优先的新建 Bot 流程：弹窗只收名称，后续凭证 / Workspace / Hermes 配置全部在
// /bots/:name/setup 向导页里逐步引导。
//
// 提交后调用 useCreateBot：name + tags=[] + 默认 domain=feishu / websocket / mention，
// 占位创建 Hermes Profile + DB 记录，但不写任何 App ID / Secret。

import { ModalForm, ProFormText } from '@ant-design/pro-components';
import { App as AntdApp, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useCreateBot } from '@/hooks/useBots';
import { zhCN } from '@/i18n/zh-CN';
import { extractErrorMessage } from '@/utils/errors';
import type { BotWizardCreateIn } from '@/api/types';

type Props = {
  open: boolean;
  onClose: () => void;
};

const NAME_RE = /^[a-z0-9][a-z0-9-]{2,31}$/;

export default function CreateBotModal({ open, onClose }: Props) {
  const { message } = AntdApp.useApp();
  const navigate = useNavigate();
  const mutate = useCreateBot();

  return (
    <ModalForm<{ name: string }>
      title={zhCN.bots.wizard.modalTitle}
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      modalProps={{ destroyOnHidden: true, maskClosable: false, width: 480 }}
      submitter={{
        searchConfig: { submitText: zhCN.bots.wizard.submitButton },
      }}
      onFinish={async (values) => {
        try {
          await mutate.mutateAsync({
            name: values.name,
            feishu_app_id: null,
            feishu_app_secret: null,
            tags: [],
            ...{
              domain: 'feishu',
              connection_mode: 'websocket',
              group_strategy: 'mention',
            },
          } as BotWizardCreateIn);
          message.success(zhCN.bots.wizard.successNavigate);
          onClose();
          navigate(`/bots/${values.name}/setup`);
          return true;
        } catch (e) {
          message.error(extractErrorMessage(e));
          return false;
        }
      }}
    >
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        先给 Bot 起个名字，下一步再在向导里完成飞书自建应用、填写凭证与
        Workspace。
      </Typography.Paragraph>
      <ProFormText
        name="name"
        label={zhCN.bots.createModal.nameLabel}
        tooltip={zhCN.bots.createModal.nameTooltip}
        rules={[
          { required: true },
          {
            validator: (_: unknown, value: string) => {
              if (!value) return Promise.resolve();
              if (value === 'default')
                return Promise.reject(new Error("不能为 'default'"));
              if (!NAME_RE.test(value))
                return Promise.reject(
                  new Error(zhCN.bots.createModal.nameRule),
                );
              return Promise.resolve();
            },
          },
        ]}
        placeholder="my-bot"
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        fieldProps={{ 'data-testid': 'create-bot-name' } as any}
      />
    </ModalForm>
  );
}
