// Phase 2-05: Placeholder page for /bots/:name/:tab quick-link routes (BOT-09).
// Phases 3-5 will replace each tab (chat / logs / skills / workspace) with its
// real implementation; until then this surfaces the route landing so navigation
// from BotCard quick links is observable end-to-end.
//
// Phase 3-02: 'setup' is a real tab now — renders WizardExecutionPage so the
// post-creation navigation /bots/{name}/setup hits the SSE wizard.

import { Button, Result, Space, Typography } from 'antd';
import type { ReactNode } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import WizardExecutionPage from './WizardExecutionPage';
import GatewayControlPanel from './GatewayControlPanel';
import LogStreamView from './LogStreamView';
import ModelConfigPage from './ModelConfigPage';
import WorkspacePage from './WorkspacePage';
import SkillsPage from './SkillsPage';
import { zhCN } from '@/i18n/zh-CN';

const VALID_TABS = ['chat', 'logs', 'skills', 'workspace', 'setup', 'gateway'] as const;
type ValidTab = (typeof VALID_TABS)[number];

const TAB_LABELS: Record<ValidTab, string> = {
  chat: '模型配置',
  logs: '日志',
  skills: 'Skills',
  workspace: 'Workspace',
  setup: '接入向导',
  gateway: 'Gateway',
};

function isValidTab(t: string | undefined): t is ValidTab {
  return !!t && (VALID_TABS as readonly string[]).includes(t);
}

function formatPlaceholder(template: string, vars: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (_, k: string) => vars[k] ?? '');
}

function DetailShell({
  name,
  tab,
  children,
}: {
  name: string;
  tab: ValidTab;
  children: ReactNode;
}) {
  return (
    <div data-testid="bot-detail-shell">
      <div style={{ padding: '24px 24px 0' }} data-testid="bot-detail-header">
        <Space direction="vertical" size={2}>
          <Typography.Text type="secondary">
            当前 Bot
          </Typography.Text>
          <Typography.Title
            level={3}
            style={{ margin: 0 }}
            data-testid="bot-detail-name"
          >
            {name}
          </Typography.Title>
          <Typography.Text type="secondary" data-testid="bot-detail-tab">
            正在配置：{TAB_LABELS[tab]}
          </Typography.Text>
        </Space>
      </div>
      {children}
    </div>
  );
}

export default function BotDetailPlaceholderPage() {
  const { name = '', tab = '' } = useParams<{ name: string; tab: string }>();
  const [searchParams] = useSearchParams();

  if (!isValidTab(tab)) {
    return (
      <div data-testid="bot-detail-unknown-tab" style={{ padding: 24 }}>
        <Result
          status="404"
          title={formatPlaceholder(zhCN.bots.detailPlaceholderUnknownTab, { tab })}
          extra={
            <Link to="/bots">
              <Button type="primary">{zhCN.bots.detailPlaceholderBack}</Button>
            </Link>
          }
        />
      </div>
    );
  }

  if (tab === 'setup') {
    return (
      <DetailShell name={name} tab={tab}>
        <WizardExecutionPage botName={name} queryParams={searchParams} />
      </DetailShell>
    );
  }

  if (tab === 'gateway') {
    return (
      <DetailShell name={name} tab={tab}>
        <GatewayControlPanel botName={name} />
      </DetailShell>
    );
  }

  if (tab === 'logs') {
    return (
      <DetailShell name={name} tab={tab}>
        <LogStreamView botName={name} />
      </DetailShell>
    );
  }

  if (tab === 'chat') {
    return (
      <DetailShell name={name} tab={tab}>
        <ModelConfigPage botName={name} />
      </DetailShell>
    );
  }

  if (tab === 'workspace') {
    return (
      <DetailShell name={name} tab={tab}>
        <WorkspacePage botName={name} />
      </DetailShell>
    );
  }

  if (tab === 'skills') {
    return (
      <DetailShell name={name} tab={tab}>
        <SkillsPage botName={name} />
      </DetailShell>
    );
  }

  return (
    <div
      data-testid={`bot-detail-placeholder-${name}-${tab}`}
      style={{ padding: 24 }}
    >
      <Result
        status="info"
        title={zhCN.bots.detailPlaceholderTitle}
        subTitle={formatPlaceholder(zhCN.bots.detailPlaceholderSubtitle, { name, tab })}
        extra={
          <Link to="/bots">
            <Button type="primary">{zhCN.bots.detailPlaceholderBack}</Button>
          </Link>
        }
      />
    </div>
  );
}
