// 引导式 Bot 配置向导（M1 MVP）。
//
// 四步合并在同一页面：
//   1. 用 SSE 流 lark-cli config init --new --lang zh，把 ASCII 二维码块
//      原样展示给用户、并把 open.feishu.cn 链接抽成可点按钮。
//   2. 用户在浏览器侧完成飞书自建应用流程后，回到此处填 App ID + App Secret，
//      调 PATCH /bots/{name}/feishu-credentials 落库。
//   3. 填 Workspace cwd，调既有 PUT /bots/{name}/workspace。
//   4. 凭证和 Workspace 都就绪后，再触发现有的 7 步 Hermes SSE
//      （/bots/{name}/wizard/run），完成后落入 WizardSuccessScreen。
//
// 注意：feishu_app_secret 永远不出现在任何 query/URL 中。

import { fetchEventSource } from '@microsoft/fetch-event-source';
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Space,
  Steps,
  Typography,
  App as AntdApp,
} from 'antd';
import { CopyOutlined } from '@ant-design/icons';
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '@/stores/auth';
import { zhCN } from '@/i18n/zh-CN';
import WizardSuccessScreen from './WizardSuccessScreen';
import { updateFeishuCredentials } from '@/api/wizard';
import { putWorkspace, getWorkspace } from '@/api/management';
import { extractErrorMessage } from '@/utils/errors';
import type { LarkInitSSEEvent, WizardSSEEvent } from '@/api/types';

const STEP_LABELS = zhCN.bots.wizard.stepLabels;

type StepState = {
  step: number;
  status: 'wait' | 'process' | 'finish' | 'error';
  message: string;
  duration_ms?: number;
  fix_hint?: string;
  error?: string;
};

function toAntdStatus(s: WizardSSEEvent['status']): StepState['status'] {
  const map: Record<string, StepState['status']> = {
    pending: 'wait',
    running: 'process',
    success: 'finish',
    error: 'error',
  };
  return map[s] ?? 'wait';
}

function FixHintAlert({
  hint,
  onRetry,
}: {
  hint: string;
  botName: string;
  onRetry: () => void;
}) {
  const t = zhCN.bots.wizard;
  const text =
    (t.fixHints as Record<string, string>)[hint] ?? t.fixHints.unknown;
  return (
    <Alert
      type="warning"
      message={text}
      action={
        <Space>
          <Button size="small" onClick={onRetry}>
            {t.retryButton}
          </Button>
        </Space>
      }
      style={{ marginTop: 8 }}
    />
  );
}

type Props = { botName: string; queryParams?: URLSearchParams };

export default function WizardExecutionPage({ botName, queryParams }: Props) {
  const [searchParams] = useSearchParams();
  const params = queryParams ?? searchParams;
  const { message: msg } = AntdApp.useApp();

  const token = useAuth((s) => s.tokens?.access_token);
  const t = zhCN.bots.wizard;

  // ---- Step 1: lark-cli SSE state ----
  const [larkOutput, setLarkOutput] = useState<string>('');
  const [larkUrl, setLarkUrl] = useState<string | null>(null);
  const [larkRunning, setLarkRunning] = useState(false);
  const [larkMissing, setLarkMissing] = useState(false);
  const [larkTimedOut, setLarkTimedOut] = useState(false);
  const larkAbortRef = useRef<AbortController | null>(null);

  const startLarkInit = () => {
    if (!token) return;
    larkAbortRef.current?.abort();
    setLarkOutput('');
    setLarkUrl(null);
    setLarkMissing(false);
    setLarkTimedOut(false);
    setLarkRunning(true);

    const ctrl = new AbortController();
    larkAbortRef.current = ctrl;
    fetchEventSource(`/api/v1/bots/${botName}/lark-app/init`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: ctrl.signal,
      openWhenHidden: true,
      onmessage(ev) {
        try {
          const data = JSON.parse(ev.data) as LarkInitSSEEvent;
          if (data.type === 'line' && data.text) {
            setLarkOutput((prev) => prev + data.text);
          } else if (data.type === 'url' && data.url) {
            setLarkUrl(data.url);
          } else if (data.type === 'missing') {
            setLarkMissing(true);
          } else if (data.type === 'timeout') {
            setLarkTimedOut(true);
            setLarkRunning(false);
          } else if (data.type === 'done') {
            setLarkRunning(false);
          }
        } catch {
          /* ignore parse errors */
        }
      },
      onerror(err) {
        setLarkRunning(false);
        throw err;
      },
    }).catch(() => {
      setLarkRunning(false);
    });
  };

  const copyBotName = async () => {
    await navigator.clipboard.writeText(botName);
    msg.success('Bot 名称已复制');
  };

  useEffect(() => {
    return () => larkAbortRef.current?.abort();
  }, []);

  // ---- Step 2: credentials form state ----
  const [credSaved, setCredSaved] = useState(false);
  const [credLoading, setCredLoading] = useState(false);
  const [credForm] = Form.useForm<{
    feishu_app_id: string;
    feishu_app_secret: string;
  }>();

  const onSaveCredentials = async () => {
    try {
      const values = await credForm.validateFields();
      setCredLoading(true);
      await updateFeishuCredentials(botName, {
        feishu_app_id: values.feishu_app_id.trim(),
        feishu_app_secret: values.feishu_app_secret,
        domain: 'feishu',
        connection_mode: 'websocket',
        group_strategy: 'mention',
      });
      setCredSaved(true);
      msg.success('飞书凭证已保存');
    } catch (e) {
      msg.error(extractErrorMessage(e));
    } finally {
      setCredLoading(false);
    }
  };

  // ---- Step 3: workspace form ----
  const [wsSaved, setWsSaved] = useState(false);
  const [wsLoading, setWsLoading] = useState(false);
  const [wsForm] = Form.useForm<{ cwd: string }>();
  useEffect(() => {
    // 预填已有 cwd（若存在）
    getWorkspace(botName)
      .then((w) => {
        if (w.cwd) {
          wsForm.setFieldsValue({ cwd: w.cwd });
          setWsSaved(true);
        }
      })
      .catch(() => undefined);
  }, [botName, wsForm]);

  const onSaveWorkspace = async () => {
    try {
      const values = await wsForm.validateFields();
      setWsLoading(true);
      await putWorkspace(botName, { cwd: values.cwd.trim() });
      setWsSaved(true);
      msg.success(zhCN.workspace.saveSuccess);
    } catch (e) {
      msg.error(extractErrorMessage(e));
    } finally {
      setWsLoading(false);
    }
  };

  // ---- Step 4: Hermes 7-step SSE ----
  const initialSteps: StepState[] = STEP_LABELS.map((label, i) => ({
    step: i + 1,
    status: 'wait' as const,
    message: label,
  }));
  const [steps, setSteps] = useState<StepState[]>(initialSteps);
  const [done, setDone] = useState(false);
  const [hermesStarted, setHermesStarted] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [failedStep, setFailedStep] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const feishuAppId = params.get('feishu_app_id') ?? '';
  const domain = params.get('domain') ?? 'feishu';
  const connectionMode = params.get('connection_mode') ?? 'websocket';
  const groupStrategy = params.get('group_strategy') ?? 'mention';

  const runWizard = () => {
    if (!token) return;
    setHermesStarted(true);
    setConnecting(true);
    setDone(false);
    setFailedStep(null);
    setSteps(initialSteps);

    const abortCtrl = new AbortController();
    abortRef.current = abortCtrl;

    const urlParams = new URLSearchParams({
      feishu_app_id: feishuAppId,
      domain,
      connection_mode: connectionMode,
      group_strategy: groupStrategy,
    });

    fetchEventSource(`/api/v1/bots/${botName}/wizard/run?${urlParams}`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: abortCtrl.signal,
      openWhenHidden: true,
      onmessage(ev) {
        const data = JSON.parse(ev.data) as WizardSSEEvent;
        if (data.status === 'done') {
          setDone(true);
          setConnecting(false);
          return;
        }
        setSteps((prev) =>
          prev.map((s) =>
            s.step === data.step
              ? {
                  ...s,
                  status: toAntdStatus(data.status),
                  message: data.message,
                  duration_ms: data.duration_ms,
                  fix_hint: data.fix_hint,
                  error: data.error,
                }
              : s,
          ),
        );
        if (data.status === 'error') {
          setFailedStep(data.step);
          setConnecting(false);
        }
      },
      onerror(err) {
        setConnecting(false);
        throw err;
      },
    }).catch(() => {
      setConnecting(false);
    });
  };

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  return (
    <div
      data-testid="wizard-execution-page"
      style={{ padding: 32, maxWidth: 760, margin: '0 auto' }}
    >
      <Typography.Title level={4}>{t.executionTitle}</Typography.Title>
      <Typography.Text type="secondary">
        {t.executionSubtitle.replace('{name}', botName)}
      </Typography.Text>

      {/* ---- Step 1: lark-cli ---- */}
      <Card title="① 创建飞书自建应用" style={{ marginTop: 24 }}>
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Typography.Paragraph style={{ marginBottom: 0 }}>
            点击下方按钮启动 <code>lark-cli config init --new --lang zh</code>。
            扫描终端二维码或点击链接，按提示在浏览器里完成自建应用，结束后回到本页填写 App ID / Secret。
          </Typography.Paragraph>
          <Alert
            type="info"
            showIcon
            message="飞书创建页不会自动带入本控制台的 Bot 名称"
            description={
              <Space direction="vertical" size={4}>
                <Typography.Text>
                  lark-cli 当前没有应用名参数。打开飞书页面后，请把应用名称/机器人名称手动改成：
                  <Typography.Text strong data-testid="lark-init-suggested-name">
                    {botName}
                  </Typography.Text>
                </Typography.Text>
                <Button
                  size="small"
                  icon={<CopyOutlined />}
                  onClick={copyBotName}
                  data-testid="lark-init-copy-name"
                >
                  复制名称
                </Button>
              </Space>
            }
          />
          <Space>
            <Button
              type="primary"
              loading={larkRunning}
              onClick={startLarkInit}
              data-testid="lark-init-start"
            >
              {larkRunning ? '运行中…' : larkOutput ? '重新运行' : '启动飞书引导'}
            </Button>
            {larkUrl && (
              <Button
                type="link"
                href={larkUrl}
                target="_blank"
                rel="noopener noreferrer"
                data-testid="lark-init-url"
              >
                在浏览器打开飞书引导
              </Button>
            )}
          </Space>
          {larkMissing && (
            <Alert
              type="error"
              message="未找到 lark-cli，请先安装：pip install lark-cli"
            />
          )}
          {larkTimedOut && (
            <Alert
              type="warning"
              message="飞书引导已超时，请重新启动二维码流程"
            />
          )}
          {larkOutput && (
            <pre
              data-testid="lark-init-output"
              style={{
                background: '#0b1021',
                color: '#e5f0ff',
                padding: 12,
                borderRadius: 4,
                fontFamily:
                  'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                fontSize: 12,
                lineHeight: 1.3,
                maxHeight: 360,
                overflow: 'auto',
                whiteSpace: 'pre',
              }}
            >
              {larkOutput}
            </pre>
          )}
        </Space>
      </Card>

      {/* ---- Step 2: credentials ---- */}
      <Card title="② 填写飞书凭证" style={{ marginTop: 16 }}>
        <Form form={credForm} layout="vertical" disabled={credSaved}>
          <Form.Item
            name="feishu_app_id"
            label="飞书 App ID"
            rules={[{ required: true, message: '请填写 App ID' }]}
          >
            <Input
              placeholder="cli_xxxxxxxxxxxxxxxx"
              data-testid="setup-app-id"
            />
          </Form.Item>
          <Form.Item
            name="feishu_app_secret"
            label="飞书 App Secret"
            rules={[{ required: true, message: '请填写 App Secret' }]}
            extra="提交后 AES-256 加密入库，永不回显明文"
          >
            <Input.Password
              autoComplete="new-password"
              data-testid="setup-app-secret"
            />
          </Form.Item>
        </Form>
        <Space>
          <Button
            type="primary"
            onClick={onSaveCredentials}
            loading={credLoading}
            disabled={credSaved}
            data-testid="setup-cred-save"
          >
            {credSaved ? '已保存' : '保存凭证'}
          </Button>
          {credSaved && (
            <Button
              size="small"
              onClick={() => setCredSaved(false)}
              data-testid="setup-cred-edit"
            >
              修改
            </Button>
          )}
        </Space>
      </Card>

      {/* ---- Step 3: workspace ---- */}
      <Card title="③ 配置 Workspace" style={{ marginTop: 16 }}>
        <Form form={wsForm} layout="vertical" disabled={wsSaved}>
          <Form.Item
            name="cwd"
            label={zhCN.workspace.cwdLabel}
            rules={[{ required: true, message: '请填写绝对路径' }]}
            extra={zhCN.workspace.cwdHint}
          >
            <Input
              placeholder="/绝对/路径/到/项目"
              data-testid="setup-workspace-cwd"
            />
          </Form.Item>
        </Form>
        <Space>
          <Button
            type="primary"
            onClick={onSaveWorkspace}
            loading={wsLoading}
            disabled={!credSaved || wsSaved}
            data-testid="setup-workspace-save"
          >
            {wsSaved ? '已保存' : '保存 Workspace'}
          </Button>
          {wsSaved && (
            <Button
              size="small"
              onClick={() => setWsSaved(false)}
              data-testid="setup-workspace-edit"
            >
              修改
            </Button>
          )}
          {!credSaved && (
            <Typography.Text type="secondary">
              请先完成第 ② 步保存凭证
            </Typography.Text>
          )}
        </Space>
      </Card>

      {/* ---- Step 4: Hermes 7-step ---- */}
      <Card title="④ 执行 Hermes 接入" style={{ marginTop: 16 }}>
        {!hermesStarted ? (
          <Space>
            <Button
              type="primary"
              disabled={!credSaved || !wsSaved}
              onClick={runWizard}
              data-testid="setup-run-hermes"
            >
              开始 Hermes 7 步配置
            </Button>
            {(!credSaved || !wsSaved) && (
              <Typography.Text type="secondary">
                请先完成上方凭证与 Workspace 步骤
              </Typography.Text>
            )}
          </Space>
        ) : (
          <>
            <Steps
              direction="vertical"
              current={-1}
              items={steps.map((s) => ({
                title: s.message,
                status: s.status,
                description:
                  s.duration_ms !== undefined ? `${s.duration_ms}ms` : undefined,
                subTitle:
                  s.fix_hint && s.status === 'error' ? (
                    <FixHintAlert
                      hint={s.fix_hint}
                      botName={botName}
                      onRetry={runWizard}
                    />
                  ) : undefined,
              }))}
            />
            {failedStep && !connecting && (
              <div style={{ marginTop: 16 }}>
                <Button type="primary" onClick={runWizard}>
                  {t.retryButton}
                </Button>
                <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                  {t.retryHint}
                </Typography.Text>
              </div>
            )}
          </>
        )}
      </Card>

      {done && <WizardSuccessScreen botName={botName} />}
    </div>
  );
}
