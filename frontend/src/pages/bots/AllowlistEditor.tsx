// Phase 4-10: Allowlist editor — empty banner, paste textarea, dedup, restart hint.
// Wired into GatewayControlPanel below PairingListInBot.

import { Alert, Button, Card, Input, Space, Typography, message } from 'antd';
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getAllowlist, putAllowlist } from '@/api/gateway';
import { zhCN } from '@/i18n/zh-CN';
import { extractErrorMessage } from '@/utils/errors';

const { TextArea } = Input;

export default function AllowlistEditor({ botName }: { botName: string }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['allowlist', botName],
    queryFn: () => getAllowlist(botName),
  });
  const users = data?.users ?? [];
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (data) setText(data.users.join('\n'));
  }, [data]);

  const parsed = text
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
  const deduped: string[] = [];
  const seen = new Set<string>();
  for (const u of parsed) {
    if (!seen.has(u)) {
      deduped.push(u);
      seen.add(u);
    }
  }
  const malformed = deduped.filter((u) => !u.startsWith('ou_'));

  const saveM = useMutation({
    mutationFn: (next: string[]) => putAllowlist(botName, next),
    onSuccess: () => {
      message.success('已保存 Allowlist');
      setSaved(true);
      setEditing(false);
      qc.invalidateQueries({ queryKey: ['allowlist', botName] });
    },
    onError: (e: unknown) => message.error(extractErrorMessage(e)),
  });

  return (
    <Card
      title={zhCN.allowlist.tabTitle}
      size="small"
      style={{ marginTop: 16 }}
      data-testid="allowlist-editor"
    >
      {isLoading && (
        <Typography.Text type="secondary">加载中...</Typography.Text>
      )}
      {!editing && !isLoading && users.length === 0 && (
        <Alert
          type="error"
          message={zhCN.allowlist.emptyBanner}
          data-testid="allowlist-empty-banner"
          action={
            <Button
              size="small"
              onClick={() => setEditing(true)}
              data-testid="btn-add-users"
            >
              {zhCN.allowlist.addBtn}
            </Button>
          }
        />
      )}
      {!editing && !isLoading && users.length > 0 && (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Text>当前 {users.length} 个用户：</Typography.Text>
          <pre
            style={{ background: '#fafafa', padding: 8, borderRadius: 4 }}
            data-testid="allowlist-users-pre"
          >
            {users.join('\n')}
          </pre>
          <Button onClick={() => setEditing(true)}>编辑</Button>
        </Space>
      )}
      {editing && (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Paragraph type="secondary">
            {zhCN.allowlist.placeholderHelp}
          </Typography.Paragraph>
          <TextArea
            rows={6}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="ou_xxx"
            data-testid="allowlist-textarea"
          />
          {malformed.length > 0 && (
            <Alert
              type="warning"
              data-testid="allowlist-warning"
              message={`以下 ${malformed.length} 项不以 ou_ 开头，可能不是合法 OpenID：${malformed.slice(0, 3).join(', ')}…`}
            />
          )}
          <Space>
            <Button
              type="primary"
              loading={saveM.isPending}
              onClick={() => saveM.mutate(deduped)}
              data-testid="btn-save-allowlist"
            >
              {zhCN.allowlist.saveBtn}（{deduped.length}）
            </Button>
            <Button
              onClick={() => {
                setEditing(false);
                setText(users.join('\n'));
              }}
            >
              取消
            </Button>
          </Space>
        </Space>
      )}
      {saved && (
        <Alert
          type="info"
          message={zhCN.allowlist.restartHint}
          style={{ marginTop: 8 }}
          data-testid="allowlist-restart-hint"
          closable
          onClose={() => setSaved(false)}
        />
      )}
    </Card>
  );
}
