// Phase 2-06: BotsPage — Card grid view of all Bots from GET /api/v1/bots.
// Wires search (BOT-02), status filter, and tag filter (B1: multi-select UI but
// only the FIRST selected tag is sent to the API in Phase 2; multi-tag intersection
// is a Phase 5 enhancement). "+ 新建 Bot" button opens CreateBotModal (Plan 02-06).

import { useMemo, useState } from 'react';
import { Alert, Button, Col, Empty, Input, Row, Select, Spin, Typography } from 'antd';
import { useBots } from '@/hooks/useBots';
import { zhCN } from '@/i18n/zh-CN';
import type { BotStatus } from '@/api/types';
import BotCard from './BotCard';
import CreateBotModal from './CreateBotModal';

type StatusFilter = 'all' | BotStatus;

export default function BotsPage() {
  const [q, setQ] = useState('');
  const [status, setStatus] = useState<StatusFilter>('all');
  // B1: multi-select tag filter UI; backend currently accepts a single `tag` query
  // param so we send the FIRST selected tag. Multi-tag intersection lands later.
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [createOpen, setCreateOpen] = useState(false);

  const { data, isLoading, error } = useBots({
    q: q || undefined,
    status: status === 'all' ? undefined : status,
    tag: selectedTags[0],
  });

  // Derive available tags from the cached payload — no extra API call.
  const availableTags = useMemo(() => {
    if (!data) return [] as string[];
    const set = new Set<string>();
    for (const bot of data) {
      for (const t of bot.tags) set.add(t);
    }
    return Array.from(set).sort();
  }, [data]);

  return (
    <div style={{ padding: 24 }}>
      <Typography.Title level={3} style={{ marginBottom: 16 }}>
        {zhCN.bots.pageTitle}
      </Typography.Title>

      <div
        style={{
          display: 'flex',
          gap: 12,
          marginBottom: 16,
          flexWrap: 'wrap',
          alignItems: 'center',
        }}
      >
        <Input.Search
          placeholder={zhCN.bots.searchPlaceholder}
          allowClear
          style={{ width: 280 }}
          onSearch={setQ}
          data-testid="bots-search"
        />
        <Select<StatusFilter>
          value={status}
          onChange={setStatus}
          data-testid="bots-status-filter"
          style={{ width: 160 }}
          options={[
            { value: 'all', label: zhCN.bots.statusFilter.all },
            { value: 'green', label: zhCN.bots.statusFilter.green },
            { value: 'yellow', label: zhCN.bots.statusFilter.yellow },
            { value: 'red', label: zhCN.bots.statusFilter.red },
            { value: 'grey', label: zhCN.bots.statusFilter.grey },
          ]}
        />
        <Select<string[]>
          mode="multiple"
          value={selectedTags}
          onChange={setSelectedTags}
          placeholder={zhCN.bots.tagFilterPlaceholder}
          allowClear
          data-testid="bots-tag-filter"
          style={{ minWidth: 200 }}
          options={availableTags.map((t) => ({ value: t, label: t }))}
        />
        <Button
          type="primary"
          data-testid="bots-create-button"
          onClick={() => setCreateOpen(true)}
        >
          {zhCN.bots.newBotButton}
        </Button>
        <span
          style={{
            marginLeft: 'auto',
            alignSelf: 'center',
            fontSize: 12,
            color: '#999',
          }}
        >
          {data ? `${zhCN.bots.countPrefix} ${data.length} ${zhCN.bots.countSuffix}` : ''}
        </span>
      </div>

      {error ? (
        <Alert
          type="error"
          message={zhCN.bots.loadErrorTitle}
          description={(error as Error).message ?? zhCN.bots.loadErrorRetry}
          data-testid="bots-error"
          style={{ marginBottom: 16 }}
        />
      ) : null}

      {isLoading ? (
        <div data-testid="bots-loading" style={{ textAlign: 'center', padding: 48 }}>
          <Spin />
        </div>
      ) : !data || data.length === 0 ? (
        <Empty
          data-testid="bots-empty"
          description={
            <div>
              <Typography.Paragraph strong>{zhCN.bots.emptyTitle}</Typography.Paragraph>
              <Typography.Paragraph type="secondary">
                {zhCN.bots.emptyDescription}
              </Typography.Paragraph>
            </div>
          }
        />
      ) : (
        <Row gutter={[16, 16]} data-testid="bots-grid">
          {data.map((bot) => (
            <Col xs={24} sm={12} md={8} key={bot.name}>
              <BotCard bot={bot} />
            </Col>
          ))}
        </Row>
      )}

      <CreateBotModal open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}
