// Phase 2-06: BotCard renders a single Bot's status + facts + 4 quick links (BOT-09).
// Quick-link targets are always /bots/{name}/{tab} — bot.name is encoded.
// Phase 2-06 adds overflow menu with Clone / Rename / Delete actions.

import { useState } from 'react';
import { Card, Dropdown, Tag, Tooltip } from 'antd';
import { MoreOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import type { BotOut } from '@/api/types';
import { zhCN } from '@/i18n/zh-CN';
import { statusToColor, statusToLabel } from './statusUtils';
import CloneBotModal from './CloneBotModal';
import RenameBotModal from './RenameBotModal';
import DeleteBotModal from './DeleteBotModal';

interface Props {
  bot: BotOut;
}

function StatusDot({ bot }: { bot: BotOut }) {
  return (
    <Tooltip title={bot.why}>
      <span
        data-testid={`status-dot-${bot.name}`}
        aria-label={`status-${bot.status}`}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
        }}
      >
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: statusToColor(bot.status),
            display: 'inline-block',
          }}
        />
        <span style={{ fontSize: 12, color: '#666' }}>{statusToLabel(bot.status)}</span>
        <span style={{ fontSize: 12, color: '#999', marginLeft: 4 }}>· {bot.why}</span>
      </span>
    </Tooltip>
  );
}

export default function BotCard({ bot }: Props) {
  const enc = encodeURIComponent(bot.name);
  const [cloneOpen, setCloneOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const menuItems = [
    { key: 'clone', label: '克隆', onClick: () => setCloneOpen(true) },
    { key: 'rename', label: '重命名', onClick: () => setRenameOpen(true) },
    { type: 'divider' as const },
    { key: 'delete', label: '删除', danger: true, onClick: () => setDeleteOpen(true) },
  ];

  return (
    <>
      <Card
        title={bot.name}
        extra={<StatusDot bot={bot} />}
        actions={[
          <Link key="chat" to={`/bots/${enc}/chat`}>
            {zhCN.bots.cardActions.chat}
          </Link>,
          <Link key="logs" to={`/bots/${enc}/logs`}>
            {zhCN.bots.cardActions.logs}
          </Link>,
          <Link key="skills" to={`/bots/${enc}/skills`}>
            {zhCN.bots.cardActions.skills}
          </Link>,
          <Link key="workspace" to={`/bots/${enc}/workspace`}>
            {zhCN.bots.cardActions.workspace}
          </Link>,
          <Dropdown key="more" menu={{ items: menuItems }} trigger={['click']}>
            <MoreOutlined data-testid={`bot-card-menu-${bot.name}`} />
          </Dropdown>,
        ]}
      >
        <div>
          {zhCN.bots.cardFields.feishuApp}：{bot.feishu_app_id ?? '—'}
        </div>
        <div>
          {zhCN.bots.cardFields.model}：{bot.model_name ?? '—'}
        </div>
        <div>
          {zhCN.bots.cardFields.skills}：{bot.skills_count}
        </div>
        <div>
          {zhCN.bots.cardFields.todayMessages}：{bot.today_message_count}
        </div>
        <div>
          {zhCN.bots.cardFields.lastHeartbeat}：{bot.last_heartbeat_at ?? '—'}
        </div>
        {bot.tags.length === 0 ? null : (
          <div data-testid={`bot-tags-${bot.name}`} style={{ marginTop: 8 }}>
            {bot.tags.map((t) => (
              <Tag key={t}>{t}</Tag>
            ))}
          </div>
        )}
      </Card>

      <CloneBotModal sourceBot={bot} open={cloneOpen} onClose={() => setCloneOpen(false)} />
      <RenameBotModal bot={bot} open={renameOpen} onClose={() => setRenameOpen(false)} />
      <DeleteBotModal bot={bot} open={deleteOpen} onClose={() => setDeleteOpen(false)} />
    </>
  );
}
