// Phase 5: simple AuditPage — table + filters over /api/v1/audit.

import {
  Button,
  Card,
  Form,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import { listAudit } from '@/api/management';
import type { AuditEntry } from '@/api/types';
import { zhCN } from '@/i18n/zh-CN';

interface FilterState {
  actor_id?: number;
  target_type?: string;
  target_id?: string;
  result?: string;
}

export default function AuditPage() {
  const [filters, setFilters] = useState<FilterState>({});
  const [form] = Form.useForm<{
    actor_id?: string;
    target_type?: string;
    target_id?: string;
    result?: string;
  }>();

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['audit', filters],
    queryFn: () => listAudit({ limit: 100, ...filters }),
  });

  const onSearch = () => {
    const values = form.getFieldsValue();
    const next: FilterState = {};
    if (values.actor_id && /^\d+$/.test(values.actor_id)) {
      next.actor_id = Number(values.actor_id);
    }
    if (values.target_type) next.target_type = values.target_type.trim();
    if (values.target_id) next.target_id = values.target_id.trim();
    if (values.result) next.result = values.result;
    setFilters(next);
  };

  const onReset = () => {
    form.resetFields();
    setFilters({});
  };

  const columns: ColumnsType<AuditEntry> = [
    {
      title: zhCN.audit.columnTime,
      dataIndex: 'created_at',
      width: 180,
      render: (v: string) => dayjs(v).format('YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: zhCN.audit.columnActor,
      dataIndex: 'actor_id',
      width: 100,
      render: (v: number | null) => (v ?? '—'),
    },
    {
      title: zhCN.audit.columnIp,
      dataIndex: 'actor_ip',
      width: 140,
      render: (v: string | null) => v ?? '—',
    },
    {
      title: zhCN.audit.columnMethod,
      dataIndex: 'method',
      width: 90,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    {
      title: zhCN.audit.columnPath,
      dataIndex: 'path',
      ellipsis: true,
    },
    {
      title: zhCN.audit.columnTarget,
      key: 'target',
      width: 180,
      render: (_, row) =>
        row.target_type ? `${row.target_type}:${row.target_id ?? ''}` : '—',
    },
    {
      title: zhCN.audit.columnResult,
      dataIndex: 'result',
      width: 100,
      render: (v: string) => (
        <Tag color={v === 'success' ? 'green' : 'red'}>{v}</Tag>
      ),
    },
    {
      title: zhCN.audit.columnError,
      dataIndex: 'error',
      ellipsis: true,
      render: (v: string | null) => v ?? '—',
    },
  ];

  return (
    <div style={{ padding: 24 }} data-testid="audit-page">
      <Typography.Title level={3}>{zhCN.audit.pageTitle}</Typography.Title>
      <Card size="small" style={{ marginBottom: 16 }}>
        <Form form={form} layout="inline" data-testid="audit-filter-form">
          <Form.Item label={zhCN.audit.filterActorIdLabel} name="actor_id">
            <Input style={{ width: 100 }} data-testid="filter-actor-id" />
          </Form.Item>
          <Form.Item label={zhCN.audit.filterTargetTypeLabel} name="target_type">
            <Input style={{ width: 140 }} data-testid="filter-target-type" />
          </Form.Item>
          <Form.Item label={zhCN.audit.filterTargetIdLabel} name="target_id">
            <Input style={{ width: 140 }} data-testid="filter-target-id" />
          </Form.Item>
          <Form.Item label={zhCN.audit.filterResultLabel} name="result">
            <Select
              style={{ width: 120 }}
              allowClear
              data-testid="filter-result"
              options={[
                { value: 'success', label: zhCN.audit.filterResultSuccess },
                { value: 'failure', label: zhCN.audit.filterResultFailure },
              ]}
            />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button
                type="primary"
                onClick={onSearch}
                loading={isLoading}
                data-testid="btn-audit-search"
              >
                查询
              </Button>
              <Button onClick={onReset}>{zhCN.audit.filterReset}</Button>
              <Button onClick={() => refetch()}>刷新</Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>
      <Table<AuditEntry>
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data ?? []}
        pagination={{ pageSize: 20 }}
        locale={{ emptyText: zhCN.audit.emptyTip }}
        data-testid="audit-table"
      />
    </div>
  );
}
