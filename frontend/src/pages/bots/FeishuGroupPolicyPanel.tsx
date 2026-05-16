import { Alert, Button, Card, Radio, Space, message } from 'antd';
import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { updateBotFeishuPolicy } from '@/api/wizard';
import { roleAtLeast, type BotFeishuPolicyPayload } from '@/api/types';
import { useRole } from '@/hooks/useRole';
import { zhCN } from '@/i18n/zh-CN';
import { extractErrorMessage } from '@/utils/errors';

type GroupStrategy = BotFeishuPolicyPayload['group_strategy'];

interface Props {
  botName: string;
  currentStrategy?: GroupStrategy;
  onSaved?: () => void;
}

export default function FeishuGroupPolicyPanel({
  botName,
  currentStrategy = 'mention',
  onSaved,
}: Props) {
  const role = useRole();
  const canEdit = roleAtLeast(role, 'Editor');
  const qc = useQueryClient();
  const [selected, setSelected] = useState<GroupStrategy>(currentStrategy);
  const [savedStrategy, setSavedStrategy] = useState<GroupStrategy>(currentStrategy);

  useEffect(() => {
    setSelected(currentStrategy);
    setSavedStrategy(currentStrategy);
  }, [currentStrategy]);

  const saveM = useMutation({
    mutationFn: (group_strategy: GroupStrategy) =>
      updateBotFeishuPolicy(botName, { group_strategy }),
    onSuccess: (bot) => {
      setSavedStrategy(bot.group_strategy ?? selected);
      message.success(zhCN.feishuGroupPolicy.saveSuccess);
      qc.invalidateQueries({ queryKey: ['bots'] });
      qc.invalidateQueries({ queryKey: ['gateway-status', botName] });
      onSaved?.();
    },
    onError: (e: unknown) => message.error(extractErrorMessage(e)),
  });

  return (
    <Card
      size="small"
      title={zhCN.feishuGroupPolicy.title}
      style={{ marginTop: 16 }}
      data-testid="feishu-group-policy-panel"
    >
      <Space direction="vertical" style={{ width: '100%' }}>
        <Radio.Group
          value={selected}
          onChange={(e) => setSelected(e.target.value as GroupStrategy)}
          disabled={!canEdit}
        >
          <Space direction="vertical">
            <Radio value="mention" data-testid="group-policy-radio-mention">
              {zhCN.feishuGroupPolicy.optionMention}
            </Radio>
            <Radio value="all" data-testid="group-policy-radio-all">
              {zhCN.feishuGroupPolicy.optionAll}
            </Radio>
            <Radio value="block" data-testid="group-policy-radio-block">
              {zhCN.feishuGroupPolicy.optionBlock}
            </Radio>
          </Space>
        </Radio.Group>

        <Alert
          type="info"
          showIcon
          message={zhCN.feishuGroupPolicy.restartHint}
        />

        <Button
          type="primary"
          data-testid="btn-save-group-policy"
          disabled={!canEdit || selected === savedStrategy}
          loading={saveM.isPending}
          onClick={() => saveM.mutate(selected)}
        >
          {zhCN.feishuGroupPolicy.saveBtn}
        </Button>
      </Space>
    </Card>
  );
}
