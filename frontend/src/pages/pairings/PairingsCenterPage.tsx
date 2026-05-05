// Phase 4-09: Global /pairings approval center (Owner/Admin only path; backend
// also enforces RBAC). Renders a ProTable of pending pairings with TTL countdown
// and inline Approve/Reject actions wired to the 04-05 REST endpoints.
//
// Per "simplicity first" project rule: no Popconfirm step — Approve/Reject
// directly invoke the mutation and surface the outcome via toast. Backend is
// the security boundary.

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Empty, Space, Tag, Typography, message } from 'antd';
import { ProTable } from '@ant-design/pro-components';
import type { ProColumns } from '@ant-design/pro-components';
import dayjs from 'dayjs';
import { approvePairing, listPairings, rejectPairing } from '@/api/pairings';
import type { PairingOut } from '@/api/types';
import { zhCN } from '@/i18n/zh-CN';
import { extractErrorMessage } from '@/utils/errors';
import { formatTtl } from './ttl';

function normalizeServerTime(value: string): string {
  return /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
}

function secondsLeft(pairing: PairingOut, dataUpdatedAt: number): number {
  if (typeof pairing.seconds_to_expiry === 'number') {
    const updatedAt = dataUpdatedAt || Date.now();
    const elapsed = Math.max(0, Math.floor((Date.now() - updatedAt) / 1000));
    return Math.max(0, pairing.seconds_to_expiry - elapsed);
  }
  return Math.max(0, dayjs(normalizeServerTime(pairing.expires_at)).diff(dayjs(), 'second'));
}

export default function PairingsCenterPage() {
  const qc = useQueryClient();
  const { data = [], dataUpdatedAt, isLoading } = useQuery({
    queryKey: ['pairings'],
    queryFn: () => listPairings(),
    refetchInterval: 10_000,
  });

  // Tick TTL columns once per second so countdown decrements visibly.
  const [, force] = useState(0);
  useEffect(() => {
    const t = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const approveM = useMutation({
    mutationFn: (id: number) => approvePairing(id),
    onSuccess: (r) => {
      message.success(r.message);
      qc.invalidateQueries({ queryKey: ['pairings'] });
    },
    onError: (e) => message.error(extractErrorMessage(e)),
  });
  const rejectM = useMutation({
    mutationFn: (id: number) => rejectPairing(id),
    onSuccess: (r) => {
      message.success(r.message);
      qc.invalidateQueries({ queryKey: ['pairings'] });
    },
    onError: (e) => message.error(extractErrorMessage(e)),
  });

  const columns: ProColumns<PairingOut>[] = [
    {
      title: zhCN.pairing.tableHeaderBot,
      dataIndex: 'bot_name',
      key: 'bot_name',
      render: (_, r) => r.bot_name ?? '-',
    },
    {
      title: zhCN.pairing.tableHeaderUser,
      dataIndex: 'feishu_user_id',
      key: 'feishu_user_id',
      render: (_, r) => r.feishu_user_id ?? '-',
    },
    {
      title: zhCN.pairing.tableHeaderCode,
      dataIndex: 'code_last4',
      key: 'code_last4',
      render: (_, r) => <Tag>{r.code_last4}</Tag>,
    },
    {
      title: zhCN.pairing.tableHeaderTtl,
      key: 'ttl',
      render: (_, r) => {
        const left = secondsLeft(r, dataUpdatedAt);
        const color = left > 60 ? 'blue' : left > 0 ? 'orange' : 'default';
        return (
          <Tag color={color} data-testid={`ttl-${r.id}`}>
            {formatTtl(left)}
          </Tag>
        );
      },
    },
    {
      title: zhCN.pairing.tableHeaderActions,
      key: 'actions',
      render: (_, r) => {
        const expired = secondsLeft(r, dataUpdatedAt) <= 0;
        return (
          <Space>
            <Button
              type="primary"
              disabled={expired || approveM.isPending}
              onClick={() => approveM.mutate(r.id)}
              data-testid={`btn-approve-${r.id}`}
            >
              {zhCN.pairing.btnApprove}
            </Button>
            <Button
              danger
              disabled={expired || rejectM.isPending}
              onClick={() => rejectM.mutate(r.id)}
              data-testid={`btn-reject-${r.id}`}
            >
              {zhCN.pairing.btnReject}
            </Button>
          </Space>
        );
      },
    },
  ];

  return (
    <div data-testid="pairings-center" style={{ padding: 24 }}>
      <Typography.Title level={3}>{zhCN.pairing.navTitle}</Typography.Title>
      <ProTable<PairingOut>
        rowKey="id"
        columns={columns}
        dataSource={data}
        loading={isLoading}
        search={false}
        toolBarRender={false}
        pagination={false}
        options={false}
        locale={{ emptyText: <Empty description={zhCN.pairing.emptyState} /> }}
      />
    </div>
  );
}
