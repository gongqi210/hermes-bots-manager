// Phase 4-09: Per-Bot pairing list embedded inside Bot detail's Gateway tab
// (D-10 dual view). Self-contained; does NOT depend on 04-07 GatewayControlPanel
// so it can land before that sibling plan completes.
//
// Per "simplicity first" project rule: Approve/Reject buttons invoke the
// mutation directly without an extra confirm dialog — backend is the auth
// boundary, and the toast surfaces the outcome.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Button,
  Card,
  Empty,
  List,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import dayjs from 'dayjs';
import { approvePairing, listPairings, rejectPairing } from '@/api/pairings';
import type { PairingOut } from '@/api/types';
import { zhCN } from '@/i18n/zh-CN';
import { extractErrorMessage } from '@/utils/errors';

function secondsLeft(expiresAt: string): number {
  return Math.max(0, dayjs(expiresAt).diff(dayjs(), 'second'));
}

export default function PairingListInBot({ botName }: { botName: string }) {
  const qc = useQueryClient();
  const queryKey = ['pairings', 'bot', botName] as const;

  const { data = [] } = useQuery({
    queryKey,
    queryFn: () => listPairings(botName),
    refetchInterval: 10_000,
  });

  const approveM = useMutation({
    mutationFn: (id: number) => approvePairing(id),
    onSuccess: (r) => {
      message.success(r.message);
      qc.invalidateQueries({ queryKey });
    },
    onError: (e) => message.error(extractErrorMessage(e)),
  });
  const rejectM = useMutation({
    mutationFn: (id: number) => rejectPairing(id),
    onSuccess: (r) => {
      message.success(r.message);
      qc.invalidateQueries({ queryKey });
    },
    onError: (e) => message.error(extractErrorMessage(e)),
  });

  return (
    <Card
      title={zhCN.pairing.navTitle}
      size="small"
      style={{ marginTop: 16 }}
      data-testid="pairing-list-in-bot"
    >
      {data.length === 0 ? (
        <Empty
          description={zhCN.pairing.emptyState}
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      ) : (
        <List<PairingOut>
          dataSource={data}
          rowKey="id"
          renderItem={(p) => {
            const left = secondsLeft(p.expires_at);
            const expired = left <= 0;
            return (
              <List.Item
                key={p.id}
                actions={[
                  <Button
                    key="ap"
                    type="primary"
                    disabled={expired || approveM.isPending}
                    onClick={() => approveM.mutate(p.id)}
                    data-testid={`btn-approve-${p.id}`}
                  >
                    {zhCN.pairing.btnApprove}
                  </Button>,
                  <Button
                    key="rj"
                    danger
                    disabled={expired || rejectM.isPending}
                    onClick={() => rejectM.mutate(p.id)}
                    data-testid={`btn-reject-${p.id}`}
                  >
                    {zhCN.pairing.btnReject}
                  </Button>,
                ]}
              >
                <Space>
                  <Tag>{p.code_last4}</Tag>
                  <Typography.Text>{p.feishu_user_id ?? '-'}</Typography.Text>
                  <Typography.Text type="secondary">
                    TTL {Math.floor(left / 60)}:{String(left % 60).padStart(2, '0')}
                  </Typography.Text>
                </Space>
              </List.Item>
            );
          }}
        />
      )}
    </Card>
  );
}
