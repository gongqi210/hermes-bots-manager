// Phase 5 plan 05-05 — AllowlistPresetPanel.
// Wraps the existing AllowlistEditor with a 3-option preset selector
// (开放测试 / 仅 Owner/Admin / 自定义) so operators can flip access mode in one
// click without manually pasting OpenIDs.

import { Alert, Button, Card, Radio, Space, Typography, message } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getAllowlistPresets, putAllowlistPreset } from '@/api/management';
import { useRole } from '@/hooks/useRole';
import { roleAtLeast, type AllowlistPreset } from '@/api/types';
import { zhCN } from '@/i18n/zh-CN';
import { extractErrorMessage } from '@/utils/errors';
import AllowlistEditor from './AllowlistEditor';

interface Props {
  botName: string;
  currentAllowlist: string[];
  onSaved?: () => void;
}

function sameMembers(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const bs = new Set(b);
  return a.every((v) => bs.has(v));
}

function detectInitialPreset(
  current: string[],
  ownerAdmin: string[] = [],
): AllowlistPreset {
  if (current.length === 0) return 'open';
  if (ownerAdmin.length > 0 && sameMembers(current, ownerAdmin)) {
    return 'owner_admin';
  }
  return 'custom';
}

export default function AllowlistPresetPanel({
  botName,
  currentAllowlist,
  onSaved,
}: Props) {
  const qc = useQueryClient();
  const role = useRole();
  const canEdit = roleAtLeast(role, 'Admin');

  const [selected, setSelected] = useState<AllowlistPreset>(
    detectInitialPreset(currentAllowlist),
  );

  const { data: presets } = useQuery({
    queryKey: ['allowlist-presets', botName],
    queryFn: () => getAllowlistPresets(botName),
  });

  const ownerAdmin = useMemo(() => presets?.owner_admin ?? [], [presets]);
  const warning = presets?.owner_admin_warning ?? null;

  useEffect(() => {
    setSelected(detectInitialPreset(currentAllowlist, ownerAdmin));
  }, [currentAllowlist, ownerAdmin]);

  const saveM = useMutation({
    mutationFn: (preset: AllowlistPreset) => putAllowlistPreset(botName, preset),
    onSuccess: () => {
      message.success(zhCN.allowlistPreset.saveSuccess);
      qc.invalidateQueries({ queryKey: ['allowlist', botName] });
      qc.invalidateQueries({ queryKey: ['allowlist-presets', botName] });
      qc.invalidateQueries({ queryKey: ['health', botName] });
      onSaved?.();
    },
    onError: (e: unknown) => message.error(extractErrorMessage(e)),
  });

  return (
    <Card
      size="small"
      title={zhCN.allowlistPreset.title}
      style={{ marginTop: 16 }}
      data-testid="allowlist-preset-panel"
    >
      <Space direction="vertical" style={{ width: '100%' }}>
        <Radio.Group
          value={selected}
          onChange={(e) => setSelected(e.target.value as AllowlistPreset)}
          disabled={!canEdit}
        >
          <Space direction="vertical">
            <Radio value="open" data-testid="preset-radio-open">
              {zhCN.allowlistPreset.optionOpen}
            </Radio>
            <Radio value="owner_admin" data-testid="preset-radio-owner_admin">
              {zhCN.allowlistPreset.optionOwnerAdmin}
            </Radio>
            <Radio value="custom" data-testid="preset-radio-custom">
              {zhCN.allowlistPreset.optionCustom}
            </Radio>
          </Space>
        </Radio.Group>

        {selected === 'open' && (
          <Alert
            type="error"
            showIcon
            data-testid="preset-open-warning"
            message={zhCN.allowlistPreset.openWarning}
          />
        )}

        {selected === 'owner_admin' && (
          <div data-testid="preset-owner-admin-list">
            {warning && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 8 }}
                message={warning}
              />
            )}
            {ownerAdmin.length > 0 ? (
              <pre
                style={{ background: '#fafafa', padding: 8, borderRadius: 4 }}
              >
                {ownerAdmin.join('\n')}
              </pre>
            ) : (
              <Typography.Text type="secondary">
                {zhCN.allowlistPreset.ownerAdminEmpty}
              </Typography.Text>
            )}
          </div>
        )}

        {selected === 'custom' && (
          <AllowlistEditor botName={botName} />
        )}

        <Button
          type="primary"
          data-testid="btn-save-preset"
          disabled={!canEdit || selected === 'custom'}
          loading={saveM.isPending}
          onClick={() => saveM.mutate(selected)}
        >
          {zhCN.allowlistPreset.saveBtn}
        </Button>
      </Space>
    </Card>
  );
}
