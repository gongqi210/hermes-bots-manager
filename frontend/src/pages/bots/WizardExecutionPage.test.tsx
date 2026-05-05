// 引导式 WizardExecutionPage 测试 — 4 步分阶段、Hermes SSE 不再自动启动。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntdApp } from 'antd';

type FetchESCall = {
  url: string;
  options: {
    headers?: Record<string, string>;
    onmessage?: (ev: { data: string }) => void;
    onerror?: (err: unknown) => void;
    signal?: AbortSignal;
    openWhenHidden?: boolean;
  };
};

const fetchESCalls: FetchESCall[] = [];

vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: vi.fn(
    async (url: string, options: FetchESCall['options']) => {
      fetchESCalls.push({ url, options });
      return new Promise<void>((resolve) => {
        options.signal?.addEventListener('abort', () => resolve());
      });
    },
  ),
}));

vi.mock('@/stores/auth', () => ({
  useAuth: vi.fn(
    (
      selector: (
        s: { tokens: { access_token: string } | null; user: unknown },
      ) => unknown,
    ) =>
      selector({
        tokens: { access_token: 'fake-jwt-token' },
        user: null,
      }),
  ),
}));

vi.mock('@/api/onboarding', () => ({
  listMyRuns: vi.fn().mockResolvedValue([]),
  markMessageReceived: vi.fn(),
}));

vi.mock('@/api/wizard', () => ({
  updateFeishuCredentials: vi.fn().mockResolvedValue({ id: 1, name: 'test-bot' }),
}));

vi.mock('@/api/management', () => ({
  putWorkspace: vi.fn().mockResolvedValue({
    bot_name: 'test-bot',
    cwd: '/tmp/x',
    exists: true,
    is_directory: true,
    readable: true,
    writable: true,
    status: 'ok',
    message: 'ok',
  }),
  getWorkspace: vi.fn().mockResolvedValue({
    bot_name: 'test-bot',
    cwd: null,
    exists: false,
    is_directory: false,
    readable: false,
    writable: false,
    status: 'unset',
    message: '',
  }),
}));

import { updateFeishuCredentials } from '@/api/wizard';
import { putWorkspace } from '@/api/management';
import WizardExecutionPage from './WizardExecutionPage';

function renderPage(botName: string, query?: string) {
  const queryParams = new URLSearchParams(
    query ?? 'feishu_app_id=cli_abc&domain=feishu',
  );
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AntdApp>
          <WizardExecutionPage botName={botName} queryParams={queryParams} />
        </AntdApp>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function emitEvent(callIndex: number, payload: object) {
  const call = fetchESCalls[callIndex];
  if (!call?.options.onmessage) throw new Error('No onmessage captured');
  await act(async () => {
    call.options.onmessage!({ data: JSON.stringify(payload) });
  });
}

beforeEach(() => {
  fetchESCalls.length = 0;
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('<WizardExecutionPage> 引导流程', () => {
  it('挂载时不自动调用 Hermes SSE', async () => {
    renderPage('test-bot');
    await waitFor(() =>
      expect(screen.getByTestId('wizard-execution-page')).toBeTruthy(),
    );
    // 没有任何 SSE 请求被发起（lark-cli 也要等点击）。
    expect(fetchESCalls.length).toBe(0);
  });

  it('点击启动飞书引导后发起 lark-cli SSE，并在收到 url 事件时显示链接', async () => {
    const user = userEvent.setup();
    renderPage('test-bot');
    await waitFor(() =>
      expect(screen.getByTestId('wizard-execution-page')).toBeTruthy(),
    );

    await user.click(screen.getByTestId('lark-init-start'));
    await waitFor(() => expect(fetchESCalls.length).toBe(1));
    expect(fetchESCalls[0].url).toContain('/api/v1/bots/test-bot/lark-app/init');

    await emitEvent(0, {
      type: 'line',
      text: '████ 二维码 ████\n',
    });
    await emitEvent(0, {
      type: 'url',
      url: 'https://open.feishu.cn/page/cli?token=abc',
    });

    await waitFor(() => {
      expect(screen.getByTestId('lark-init-output').textContent).toContain(
        '二维码',
      );
      const link = screen.getByTestId('lark-init-url') as HTMLAnchorElement;
      expect(link.href).toContain('open.feishu.cn');
    });
  });

  it('展示需要手动填入飞书创建页的 Bot 名称', async () => {
    renderPage('test-bot');
    await waitFor(() =>
      expect(screen.getByTestId('wizard-execution-page')).toBeTruthy(),
    );

    expect(screen.getByTestId('lark-init-suggested-name').textContent).toBe(
      'test-bot',
    );
    expect(screen.getByTestId('lark-init-copy-name')).toBeTruthy();
  });

  it('在凭证未保存时无法触发 Hermes 7 步配置', async () => {
    renderPage('test-bot');
    await waitFor(() =>
      expect(screen.getByTestId('wizard-execution-page')).toBeTruthy(),
    );
    const runBtn = screen.getByTestId('setup-run-hermes') as HTMLButtonElement;
    expect(runBtn.disabled).toBe(true);
  });

  it('保存凭证 → 保存 Workspace → 启动 Hermes SSE，URL 不带 secret', async () => {
    const user = userEvent.setup();
    renderPage(
      'test-bot',
      'feishu_app_id=cli_xyz&feishu_app_secret=THIS_SHOULD_NOT_LEAK&domain=feishu',
    );
    await waitFor(() =>
      expect(screen.getByTestId('wizard-execution-page')).toBeTruthy(),
    );

    // ② 凭证
    await user.type(screen.getByTestId('setup-app-id'), 'cli_abc1234567');
    await user.type(screen.getByTestId('setup-app-secret'), 'super-secret');
    await user.click(screen.getByTestId('setup-cred-save'));
    await waitFor(() =>
      expect(updateFeishuCredentials).toHaveBeenCalledWith(
        'test-bot',
        expect.objectContaining({
          feishu_app_id: 'cli_abc1234567',
          feishu_app_secret: 'super-secret',
        }),
      ),
    );

    // ③ Workspace
    await user.type(screen.getByTestId('setup-workspace-cwd'), '/tmp/proj');
    await user.click(screen.getByTestId('setup-workspace-save'));
    await waitFor(() =>
      expect(putWorkspace).toHaveBeenCalledWith('test-bot', { cwd: '/tmp/proj' }),
    );

    // ④ Hermes
    const runBtn = await screen.findByTestId('setup-run-hermes');
    await user.click(runBtn);

    await waitFor(() => expect(fetchESCalls.length).toBe(1));
    const url = fetchESCalls[0].url;
    expect(url).toContain('/api/v1/bots/test-bot/wizard/run');
    expect(url).toContain('feishu_app_id=cli_xyz');
    expect(url).not.toContain('feishu_app_secret');
    expect(url).not.toContain('THIS_SHOULD_NOT_LEAK');
  });

  it('Hermes SSE 收到 done 后展示成功界面', async () => {
    const user = userEvent.setup();
    renderPage('test-bot');
    await waitFor(() =>
      expect(screen.getByTestId('wizard-execution-page')).toBeTruthy(),
    );

    await user.type(screen.getByTestId('setup-app-id'), 'cli_abc1234567');
    await user.type(screen.getByTestId('setup-app-secret'), 'super-secret');
    await user.click(screen.getByTestId('setup-cred-save'));
    await waitFor(() => expect(updateFeishuCredentials).toHaveBeenCalled());

    await user.type(screen.getByTestId('setup-workspace-cwd'), '/tmp/proj');
    await user.click(screen.getByTestId('setup-workspace-save'));
    await waitFor(() => expect(putWorkspace).toHaveBeenCalled());

    await user.click(await screen.findByTestId('setup-run-hermes'));
    await waitFor(() => expect(fetchESCalls.length).toBe(1));

    await emitEvent(0, { step: 0, status: 'done', message: '向导完成' });

    await waitFor(() => {
      expect(screen.getByTestId('wizard-success-screen')).toBeTruthy();
    });
  });
});
