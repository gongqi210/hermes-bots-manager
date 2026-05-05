// FEISHU-07: First-pairing guidance shown after all 7 wizard steps succeed.
// Phase 4-10: extended with 3-minute KPI mark-message-received button (D-19).
import {
  Button,
  Card,
  Steps,
  Typography,
  Space,
  Tooltip,
  message,
} from 'antd';
import { CheckCircleOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { zhCN } from '@/i18n/zh-CN';
import { listMyRuns, markMessageReceived } from '@/api/onboarding';
import { extractErrorMessage } from '@/utils/errors';

type Props = { botName: string };

export default function WizardSuccessScreen({ botName }: Props) {
  const t = zhCN.bots.wizard;
  const qc = useQueryClient();

  const { data: runs = [] } = useQuery({
    queryKey: ['onboarding-runs'],
    queryFn: () => listMyRuns(5),
  });
  const inProgress = runs.find((r) => r.status === 'in_progress');

  const markM = useMutation({
    mutationFn: (id: number) => markMessageReceived(id),
    onSuccess: () => {
      const seconds = inProgress
        ? Math.max(
            0,
            Math.round(
              (Date.now() - new Date(inProgress.started_at).getTime()) / 1000,
            ),
          )
        : 0;
      message.success(`已记录 ${seconds} 秒`);
      qc.invalidateQueries({ queryKey: ['onboarding-runs'] });
    },
    onError: (e: unknown) => message.error(extractErrorMessage(e)),
  });

  return (
    <Card
      data-testid="wizard-success-screen"
      style={{ marginTop: 32, maxWidth: 560 }}
    >
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <div style={{ textAlign: 'center' }}>
          <CheckCircleOutlined style={{ fontSize: 48, color: '#52c41a' }} />
          <Typography.Title level={4} style={{ marginTop: 8 }}>
            {t.successTitle}
          </Typography.Title>
          <Typography.Text type="secondary">
            {t.successSubtitle.replace('{name}', botName)}
          </Typography.Text>
        </div>

        <Steps
          direction="vertical"
          current={-1}
          items={[
            { title: t.pairingStep1, status: 'wait' },
            {
              title: t.pairingStep2,
              status: 'wait',
              description: (
                <Typography.Text type="secondary">{t.pairingNote}</Typography.Text>
              ),
            },
            { title: t.pairingStep3, status: 'wait' },
          ]}
        />

        <Space>
          <Link to={`/bots/${botName}/logs`}>
            <Button type="primary">{t.viewBotButton}</Button>
          </Link>
          <Link to="/bots">
            <Button>{t.backToListButton}</Button>
          </Link>
        </Space>

        <Card size="small" title="完成 3-minute KPI 测试" data-testid="kpi-card">
          <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
            在飞书群里 @ 机器人收到第一条回复后，请点击下方按钮记录耗时。
          </Typography.Paragraph>
          <Tooltip title={inProgress ? '' : '未发现进行中的接入流程'}>
            <span>
              <Button
                type="primary"
                disabled={!inProgress || markM.isPending}
                loading={markM.isPending}
                onClick={() => inProgress && markM.mutate(inProgress.id)}
                data-testid="btn-mark-message-received"
              >
                我已收到第一条回复
              </Button>
            </span>
          </Tooltip>
        </Card>
      </Space>
    </Card>
  );
}
