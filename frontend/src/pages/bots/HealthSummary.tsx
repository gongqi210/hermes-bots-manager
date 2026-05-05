// Phase 5: small health summary banner that shows on chat / workspace / skills
// pages. Combines gateway state, model-configured flag, workspace status and
// skills enabled/total into a single ribbon so operators don't have to bounce
// between tabs to see what's broken.

import { Alert, Descriptions, Skeleton, Tag } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { getHealth } from '@/api/management';
import type { HealthOut } from '@/api/types';
import { zhCN } from '@/i18n/zh-CN';

const STATE_COLOR: Record<HealthOut['gateway_state'], string> = {
  running: 'green',
  starting: 'gold',
  stopped: 'default',
  error: 'red',
  unconfigured: 'default',
};

const WORKSPACE_COLOR: Record<HealthOut['workspace_status'], string> = {
  ok: 'green',
  warning: 'gold',
  error: 'red',
  unset: 'default',
};

function formatPlaceholder(template: string, vars: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (_, k: string) => vars[k] ?? '');
}

export default function HealthSummary({ botName }: { botName: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['health', botName],
    queryFn: () => getHealth(botName),
    refetchInterval: 15_000,
  });

  if (isLoading || !data) {
    return (
      <div style={{ marginBottom: 16 }} data-testid="health-summary-loading">
        <Skeleton active paragraph={{ rows: 1 }} />
      </div>
    );
  }

  const overall = data.overall;
  const overallType =
    overall === 'ok' ? 'success' : overall === 'warning' ? 'warning' : 'error';
  const overallText =
    overall === 'ok'
      ? zhCN.health.overallOk
      : overall === 'warning'
        ? zhCN.health.overallWarning
        : zhCN.health.overallError;

  return (
    <Alert
      style={{ marginBottom: 16 }}
      type={overallType}
      showIcon
      data-testid={`health-summary-${overall}`}
      message={`${zhCN.health.title} · ${overallText}`}
      description={
        <Descriptions size="small" column={2} colon={false}>
          <Descriptions.Item label={zhCN.health.gatewayLabel}>
            <Tag color={STATE_COLOR[data.gateway_state]} data-testid="health-gateway-state">
              {data.gateway_state}
            </Tag>
            <span style={{ color: 'rgba(0,0,0,0.5)', marginLeft: 8 }}>
              {data.gateway_why}
            </span>
          </Descriptions.Item>
          <Descriptions.Item label={zhCN.health.modelLabel}>
            <Tag color={data.model_configured ? 'green' : 'red'}>
              {data.model_configured
                ? zhCN.health.modelConfigured
                : zhCN.health.modelMissing}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label={zhCN.health.workspaceLabel}>
            <Tag color={WORKSPACE_COLOR[data.workspace_status]}>
              {data.workspace_status}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label={zhCN.health.skillsLabel}>
            {formatPlaceholder(zhCN.health.skillsRatio, {
              enabled: String(data.skills_enabled),
              total: String(data.skills_total),
            })}
            {data.dangerous_skill_count > 0 && (
              <Tag
                color="red"
                style={{ marginLeft: 8 }}
                data-testid="health-dangerous-badge"
              >
                {zhCN.health.dangerousLabel}: {data.dangerous_skill_count}
              </Tag>
            )}
            {data.shadowed_skill_count > 0 && (
              <Tag
                color="orange"
                style={{ marginLeft: 8 }}
                data-testid="health-shadowed-badge"
              >
                {zhCN.health.shadowedLabel}: {data.shadowed_skill_count}
              </Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label={zhCN.allowlistPreset.title}>
            <Tag data-testid="health-preset-tag">
              {data.allowlist_preset === 'open'
                ? zhCN.health.presetOpen
                : data.allowlist_preset === 'owner_admin'
                  ? zhCN.health.presetOwnerAdmin
                  : zhCN.health.presetCustom}
            </Tag>
          </Descriptions.Item>
        </Descriptions>
      }
    />
  );
}
