// Phase 5: Skills page (skills tab) — bulk select, source/status filters,
// dangerous-skill confirmation modal, shadow tag, missing-deps alert, and
// Owner-only zip upload via authenticated axios.

import {
  Alert,
  Button,
  Card,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd';
import type { UploadProps } from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import { getSkills, putSkills, uploadSkill } from '@/api/management';
import type { SkillItem } from '@/api/types';
import { useRole } from '@/hooks/useRole';
import { zhCN } from '@/i18n/zh-CN';
import { extractErrorMessage } from '@/utils/errors';
import HealthSummary from './HealthSummary';

interface PendingEnable {
  skillNames: string[]; // dangerous skills being enabled
  nextDisabled: string[];
}

type SourceFilter = 'all' | 'profile' | 'global' | 'uploaded';
type StatusFilter = 'all' | 'enabled' | 'disabled' | 'shadowed';

function formatPlaceholder(template: string, vars: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (_, k: string) => vars[k] ?? '');
}

export default function SkillsPage({ botName }: { botName: string }) {
  const qc = useQueryClient();
  const role = useRole();
  const [search, setSearch] = useState('');
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [pending, setPending] = useState<PendingEnable | null>(null);
  const [confirmText, setConfirmText] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['skills', botName],
    queryFn: () => getSkills(botName),
  });

  const saveM = useMutation({
    mutationFn: (payload: { disabled: string[]; confirm_name?: string }) =>
      putSkills(botName, payload),
    onSuccess: () => {
      message.success(zhCN.skills.saveSuccess);
      qc.invalidateQueries({ queryKey: ['skills', botName] });
      qc.invalidateQueries({ queryKey: ['health', botName] });
      setSelectedRowKeys([]);
    },
    onError: (e: unknown) => message.error(extractErrorMessage(e)),
  });

  const filtered = useMemo(() => {
    const skills = data?.skills ?? [];
    const s = search.trim().toLowerCase();
    return skills.filter((sk) => {
      if (s) {
        const hay = `${sk.name} ${sk.category ?? ''} ${sk.description ?? ''}`.toLowerCase();
        if (!hay.includes(s)) return false;
      }
      if (sourceFilter !== 'all' && sk.source !== sourceFilter) return false;
      if (statusFilter === 'enabled' && !sk.enabled) return false;
      if (statusFilter === 'disabled' && sk.enabled) return false;
      if (statusFilter === 'shadowed' && !sk.shadowed_source) return false;
      return true;
    });
  }, [data?.skills, search, sourceFilter, statusFilter]);

  const skillsByName = useMemo(() => {
    const map = new Map<string, SkillItem>();
    (data?.skills ?? []).forEach((sk) => map.set(sk.name, sk));
    return map;
  }, [data?.skills]);

  const onToggle = (skill: SkillItem, nextEnabled: boolean) => {
    if (!data) return;
    const currentDisabled = new Set(data.disabled);
    if (nextEnabled) currentDisabled.delete(skill.name);
    else currentDisabled.add(skill.name);
    const nextDisabled = Array.from(currentDisabled).sort();

    if (nextEnabled && skill.dangerous) {
      setPending({ skillNames: [skill.name], nextDisabled });
      setConfirmText('');
      return;
    }
    saveM.mutate({ disabled: nextDisabled });
  };

  const onBulkSet = (enable: boolean) => {
    if (!data) return;
    const currentDisabled = new Set(data.disabled);
    const dangerousEnables: string[] = [];
    selectedRowKeys.forEach((k) => {
      const name = String(k);
      const sk = skillsByName.get(name);
      if (!sk) return;
      if (enable) {
        if (!sk.enabled && sk.dangerous) dangerousEnables.push(name);
        currentDisabled.delete(name);
      } else {
        currentDisabled.add(name);
      }
    });
    const nextDisabled = Array.from(currentDisabled).sort();

    if (enable && dangerousEnables.length > 0) {
      setPending({ skillNames: dangerousEnables, nextDisabled });
      setConfirmText('');
      return;
    }
    saveM.mutate({ disabled: nextDisabled });
  };

  const submitDangerousEnable = () => {
    if (!pending) return;
    if (confirmText.trim() !== botName) {
      message.error('Bot 名输入不一致');
      return;
    }
    saveM.mutate({
      disabled: pending.nextDisabled,
      confirm_name: botName,
    });
    setPending(null);
    setConfirmText('');
  };

  const sourceLabel = (v: SkillItem['source']): string =>
    v === 'profile'
      ? zhCN.skills.sourceProfile
      : v === 'global'
        ? zhCN.skills.sourceGlobal
        : '上传';

  const columns: ColumnsType<SkillItem> = [
    {
      title: zhCN.skills.columnEnabled,
      key: 'enabled',
      width: 80,
      render: (_, row) => (
        <Switch
          checked={row.enabled}
          loading={saveM.isPending}
          onChange={(checked) => onToggle(row, checked)}
          data-testid={`switch-skill-${row.name}`}
          aria-label={`${row.name} ${row.enabled ? '已启用' : '已禁用'}`}
        />
      ),
    },
    {
      title: zhCN.skills.columnName,
      dataIndex: 'name',
      key: 'name',
      render: (name: string, row) => (
        <Space size={4} direction="vertical" style={{ width: '100%' }}>
          <Space size={4}>
            <Typography.Text strong>{name}</Typography.Text>
            {row.dangerous && (
              <Tooltip title="启用前需输入 Bot 名以确认风险">
                <Tag color="red" data-testid={`dangerous-tag-${row.name}`}>
                  {zhCN.skills.dangerousTag}
                </Tag>
              </Tooltip>
            )}
          </Space>
          {row.missing_deps && row.missing_deps.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message={`缺少系统依赖：${row.missing_deps.join(', ')} — 请在服务器上安装后重启 Gateway`}
              data-testid={`missing-deps-${row.name}`}
              style={{ padding: '4px 8px' }}
            />
          )}
        </Space>
      ),
    },
    {
      title: zhCN.skills.columnCategory,
      dataIndex: 'category',
      key: 'category',
      render: (v: string | null) => v ?? '—',
    },
    {
      title: zhCN.skills.columnDescription,
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (v: string | null) => v ?? '—',
    },
    {
      title: zhCN.skills.columnSource,
      dataIndex: 'source',
      key: 'source',
      width: 160,
      render: (v: SkillItem['source'], row) => (
        <Space size={4} wrap>
          <Tag>{sourceLabel(v)}</Tag>
          {row.shadowed_source && (
            <Tooltip title="Profile 本地同名技能优先，此条目不生效">
              <Tag color="orange" data-testid={`shadowed-tag-${row.name}`}>
                被遮蔽 (来源: {row.shadowed_source})
              </Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
  ];

  const uploadProps: UploadProps = {
    accept: '.zip',
    showUploadList: false,
    customRequest: async (opts) => {
      const file = opts.file as File;
      try {
        await uploadSkill(botName, file);
        message.success('Skill 已上传并安装');
        qc.invalidateQueries({ queryKey: ['skills', botName] });
        qc.invalidateQueries({ queryKey: ['health', botName] });
        opts.onSuccess?.({}, new XMLHttpRequest());
      } catch (e) {
        const msg = extractErrorMessage(e);
        message.error(msg);
        opts.onError?.(new Error(msg));
      }
    },
  };

  const isOwner = role === 'Owner';

  return (
    <div style={{ padding: 24 }} data-testid="skills-page">
      <HealthSummary botName={botName} />
      <Card title={zhCN.skills.tabTitle} size="small">
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Space wrap>
            <Input.Search
              placeholder={zhCN.skills.searchPlaceholder}
              allowClear
              onChange={(e) => setSearch(e.target.value)}
              data-testid="input-skills-search"
              style={{ width: 280 }}
            />
            <Select<SourceFilter>
              value={sourceFilter}
              onChange={setSourceFilter}
              data-testid="select-source-filter"
              style={{ width: 140 }}
              options={[
                { value: 'all', label: '全部来源' },
                { value: 'profile', label: '本 Bot' },
                { value: 'global', label: '全局' },
                { value: 'uploaded', label: '上传' },
              ]}
            />
            <Select<StatusFilter>
              value={statusFilter}
              onChange={setStatusFilter}
              data-testid="select-status-filter"
              style={{ width: 140 }}
              options={[
                { value: 'all', label: '全部状态' },
                { value: 'enabled', label: '已启用' },
                { value: 'disabled', label: '已禁用' },
                { value: 'shadowed', label: '被遮蔽' },
              ]}
            />
            {isOwner && (
              <Upload {...uploadProps}>
                <Button icon={<UploadOutlined />} data-testid="btn-upload-skill">
                  上传 Skill
                </Button>
              </Upload>
            )}
          </Space>

          {selectedRowKeys.length > 0 && (
            <Space data-testid="bulk-action-bar">
              <Typography.Text>已选 {selectedRowKeys.length} 项：</Typography.Text>
              <Button
                type="primary"
                onClick={() => onBulkSet(true)}
                data-testid="btn-bulk-enable"
              >
                批量启用
              </Button>
              <Button onClick={() => onBulkSet(false)} data-testid="btn-bulk-disable">
                批量禁用
              </Button>
              <Button
                type="link"
                onClick={() => setSelectedRowKeys([])}
                data-testid="btn-bulk-clear"
              >
                取消选择
              </Button>
            </Space>
          )}

          <Table<SkillItem>
            rowKey="name"
            size="small"
            loading={isLoading}
            columns={columns}
            dataSource={filtered}
            pagination={false}
            locale={{ emptyText: zhCN.skills.emptyState }}
            rowSelection={{
              selectedRowKeys,
              onChange: (keys) => setSelectedRowKeys(keys),
            }}
            data-testid="skills-table"
          />
        </Space>
      </Card>
      <Modal
        open={pending !== null}
        title={zhCN.skills.enableConfirmTitle}
        okText={zhCN.skills.enableConfirmOk}
        cancelText={zhCN.skills.enableConfirmCancel}
        onCancel={() => {
          setPending(null);
          setConfirmText('');
        }}
        onOk={submitDangerousEnable}
        okButtonProps={{
          danger: true,
          disabled: confirmText.trim() !== botName,
          'data-testid': 'btn-confirm-dangerous',
        }}
      >
        <Typography.Paragraph>
          {pending && pending.skillNames.length === 1
            ? formatPlaceholder(zhCN.skills.enableConfirmBody, {
                name: botName,
                skill: pending.skillNames[0],
              })
            : pending
              ? `以下高风险 Skill 将被启用，请输入 Bot 名 "${botName}" 确认：`
              : ''}
        </Typography.Paragraph>
        {pending && (
          <ul data-testid="dangerous-skill-list">
            {pending.skillNames.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        )}
        <Input
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
          placeholder={zhCN.skills.enableConfirmInputPlaceholder}
          data-testid="input-dangerous-confirm"
        />
      </Modal>
    </div>
  );
}
