// Phase 4-07: Gateway control panel — pill + Start/Stop/Restart + log tail drawer.
// Wired into BotDetailPlaceholderPage 'gateway' tab.

import {
  Button,
  Drawer,
  Form,
  Input,
  message,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Typography,
} from 'antd';
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useState } from 'react';
import { gatewayAction, getAllowlist, getGatewayStatus } from '@/api/gateway';
import PairingListInBot from './PairingListInBot';
import AllowlistPresetPanel from './AllowlistPresetPanel';
import { GatewayStatusPill } from './GatewayStatusPill';
import { useRole } from '@/hooks/useRole';
import { zhCN } from '@/i18n/zh-CN';
import type { GatewayActionResponse } from '@/api/types';
import { extractErrorMessage } from '@/utils/errors';

type GatewayActionType = 'start' | 'stop' | 'restart';

export default function GatewayControlPanel({ botName }: { botName: string }) {
  const role = useRole();
  const canControl = !!role && ['Owner', 'Admin', 'Editor'].includes(role);
  const qc = useQueryClient();
  const [logTail, setLogTail] = useState<string[] | null>(null);
  const [stopForm] = Form.useForm<{ confirmName: string }>();
  const [stopOpen, setStopOpen] = useState(false);

  const statusQ = useQuery({
    queryKey: ['gateway-status', botName],
    queryFn: () => getGatewayStatus(botName),
    refetchInterval: 5_000,
  });

  const allowlistQ = useQuery({
    queryKey: ['allowlist', botName],
    queryFn: () => getAllowlist(botName),
  });

  const actionM = useMutation({
    mutationFn: (action: GatewayActionType) => gatewayAction(botName, action),
    onSuccess: (resp: GatewayActionResponse) => {
      setLogTail(resp.recent_log_tail);
      message.success(
        zhCN.gateway.actionSuccess
          .replace('{action}', resp.action)
          .replace('{state}', resp.new_state),
      );
      qc.invalidateQueries({ queryKey: ['gateway-status', botName] });
    },
    onError: (e: unknown) => {
      const status = (e as { response?: { status?: number } })?.response
        ?.status;
      if (status === 503) {
        message.error(zhCN.gateway.busy);
      } else {
        message.error(extractErrorMessage(e));
      }
    },
  });

  const onStopConfirm = async () => {
    try {
      await stopForm.validateFields();
      setStopOpen(false);
      actionM.mutate('stop');
      stopForm.resetFields();
    } catch {
      /* validation errors stay inline */
    }
  };

  return (
    <div data-testid="gateway-control-panel" style={{ padding: 24 }}>
      <Typography.Title level={4}>{zhCN.gateway.tabTitle}</Typography.Title>
      {statusQ.isLoading && <Spin />}
      {statusQ.data && <GatewayStatusPill status={statusQ.data} />}

      <Space style={{ marginTop: 16 }}>
        <Popconfirm
          title={zhCN.gateway.confirmStartTitle}
          okText={zhCN.common.confirm}
          cancelText={zhCN.common.cancel}
          onConfirm={() => actionM.mutate('start')}
          disabled={!canControl}
        >
          <Button
            type="primary"
            disabled={!canControl}
            loading={actionM.isPending && actionM.variables === 'start'}
            data-testid="btn-gateway-start"
          >
            {zhCN.gateway.btnStart}
          </Button>
        </Popconfirm>
        <Popconfirm
          title={zhCN.gateway.confirmRestartTitle}
          okText={zhCN.common.confirm}
          cancelText={zhCN.common.cancel}
          onConfirm={() => actionM.mutate('restart')}
          disabled={!canControl}
        >
          <Button
            disabled={!canControl}
            loading={actionM.isPending && actionM.variables === 'restart'}
            data-testid="btn-gateway-restart"
          >
            {zhCN.gateway.btnRestart}
          </Button>
        </Popconfirm>
        <Button
          danger
          onClick={() => setStopOpen(true)}
          disabled={!canControl}
          loading={actionM.isPending && actionM.variables === 'stop'}
          data-testid="btn-gateway-stop"
        >
          {zhCN.gateway.btnStop}
        </Button>
      </Space>

      <PairingListInBot botName={botName} />

      <AllowlistPresetPanel
        botName={botName}
        currentAllowlist={allowlistQ.data?.users ?? []}
      />

      <Modal
        title={zhCN.gateway.confirmStopTitle}
        open={stopOpen}
        onOk={onStopConfirm}
        onCancel={() => {
          setStopOpen(false);
          stopForm.resetFields();
        }}
        okButtonProps={{ danger: true }}
        okText={zhCN.common.confirm}
        cancelText={zhCN.common.cancel}
      >
        <Typography.Paragraph>
          {zhCN.gateway.confirmStopBody.replace('{name}', botName)}
        </Typography.Paragraph>
        <Form form={stopForm} layout="vertical">
          <Form.Item
            name="confirmName"
            rules={[
              {
                required: true,
                validator: (_, v: string) =>
                  v === botName
                    ? Promise.resolve()
                    : Promise.reject(new Error(`请输入 "${botName}"`)),
              },
            ]}
          >
            <Input placeholder={botName} data-testid="stop-confirm-input" />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={zhCN.gateway.logTailDrawerTitle}
        placement="right"
        width={720}
        open={logTail !== null}
        onClose={() => setLogTail(null)}
      >
        <pre
          data-testid="log-tail-pre"
          style={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}
        >
          {logTail?.join('\n')}
        </pre>
      </Drawer>
    </div>
  );
}
