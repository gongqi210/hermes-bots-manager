// Phase 4-08 Task 2: realtime log viewer for the `logs` tab.
// Pauses/resumes via Switch, filters via keyword input (comma-separated),
// auto-scrolls by default, and offers a 1h/6h/24h/72h download dropdown.

import { Alert, Button, Dropdown, Input, Space, Switch, Typography } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useGatewayWebSocket } from '@/api/useGatewayWebSocket';
import { downloadLogsUrl } from '@/api/gateway';
import { zhCN } from '@/i18n/zh-CN';

const LEVEL_COLOR: Record<string, string> = {
  debug: '#888',
  info: '#222',
  warn: '#d48806',
  warning: '#d48806',
  error: '#cf1322',
  critical: '#a8071a',
};

const MAX_LINES = 5000;

export default function LogStreamView({ botName }: { botName: string }) {
  const [paused, setPaused] = useState(false);
  const [filterText, setFilterText] = useState('');
  const [autoScroll, setAutoScroll] = useState(true);
  const preRef = useRef<HTMLPreElement>(null);

  const keywords = useMemo(
    () =>
      filterText
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
    [filterText],
  );

  const { lines, droppedCount, isConnected, error, clear } = useGatewayWebSocket(
    botName,
    { keywords, levelMin: 'info', paused },
  );

  useEffect(() => {
    if (autoScroll && preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight;
    }
  }, [lines, autoScroll]);

  const onScroll = () => {
    if (!preRef.current) return;
    const el = preRef.current;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
    if (!atBottom) setAutoScroll(false);
  };

  const downloadItems = ([1, 6, 24, 72] as const).map((h) => ({
    key: String(h),
    label: zhCN.logs[`downloadOpt${h}h` as `downloadOpt${typeof h}h`],
    onClick: () => {
      window.location.href = downloadLogsUrl(botName, h);
    },
  }));

  return (
    <div
      data-testid="log-stream-view"
      style={{
        padding: 16,
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Space style={{ marginBottom: 12 }} wrap>
        <Switch
          checkedChildren={zhCN.logs.btnPause}
          unCheckedChildren={zhCN.logs.btnResume}
          checked={!paused}
          onChange={(checked) => setPaused(!checked)}
          data-testid="pause-toggle"
        />
        <Input.Search
          placeholder={zhCN.logs.filterPlaceholder}
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
          style={{ width: 300 }}
          data-testid="keyword-filter"
          allowClear
        />
        <Button onClick={clear} data-testid="btn-clear">
          {zhCN.logs.btnClear}
        </Button>
        <Button
          data-testid="autoscroll-btn"
          onClick={() => setAutoScroll(true)}
          type={autoScroll ? 'default' : 'primary'}
        >
          {autoScroll ? '🔽 自动滚动' : '▶ 恢复滚动'}
        </Button>
        <Dropdown menu={{ items: downloadItems }} trigger={['click']}>
          <Button icon={<DownloadOutlined />} data-testid="btn-download">
            {zhCN.logs.downloadBtn}
          </Button>
        </Dropdown>
        <Typography.Text type={isConnected ? 'success' : 'danger'}>
          {isConnected ? '● connected' : '○ disconnected'}
        </Typography.Text>
      </Space>

      {lines.length >= MAX_LINES && (
        <Alert
          type="info"
          message={zhCN.logs.truncatedNote}
          style={{ marginBottom: 8 }}
        />
      )}
      {droppedCount > 0 && (
        <Alert
          type="warning"
          message={zhCN.logs.droppedMarker.replace(
            '{count}',
            String(droppedCount),
          )}
          style={{ marginBottom: 8 }}
        />
      )}
      {error && (
        <Alert type="error" message={error} style={{ marginBottom: 8 }} />
      )}

      <pre
        ref={preRef}
        onScroll={onScroll}
        data-testid="log-pre"
        style={{
          flex: 1,
          fontFamily: 'monospace',
          fontSize: 12,
          lineHeight: 1.4,
          background: '#fafafa',
          padding: 12,
          overflow: 'auto',
          margin: 0,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
          minHeight: 240,
        }}
      >
        {lines.map((l, i) => (
          <div
            key={i}
            data-level={l.level}
            style={{ color: LEVEL_COLOR[l.level] ?? '#222' }}
          >
            <span style={{ opacity: 0.5, marginRight: 6 }}>
              {l.ts.slice(11, 19)}
            </span>
            <span>{l.text}</span>
          </div>
        ))}
      </pre>
    </div>
  );
}
