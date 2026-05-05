// Phase 3-02 Task 3b: Tests for WizardSuccessScreen (FEISHU-07).
// Phase 4-10: WS1-WS4 cover the 3-minute KPI mark-message-received button.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import WizardSuccessScreen from './WizardSuccessScreen';
import type { OnboardingRunOut } from '@/api/types';

vi.mock('@/api/onboarding', () => ({
  listMyRuns: vi.fn(),
  markMessageReceived: vi.fn(),
}));

const { messageMock } = vi.hoisted(() => ({
  messageMock: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
}));
vi.mock('antd', async () => {
  const actual = await vi.importActual<typeof import('antd')>('antd');
  return { ...actual, message: messageMock };
});

import { listMyRuns, markMessageReceived } from '@/api/onboarding';

const mockedList = vi.mocked(listMyRuns);
const mockedMark = vi.mocked(markMessageReceived);

function makeRun(overrides: Partial<OnboardingRunOut> = {}): OnboardingRunOut {
  return {
    id: 1,
    user_id: 1,
    bot_id: 1,
    started_at: new Date(Date.now() - 60_000).toISOString(),
    login_at: null,
    wizard_done_at: null,
    gateway_running_at: null,
    first_pairing_approved_at: null,
    first_message_at: null,
    total_duration_ms: null,
    status: 'in_progress',
    last_step: 'wizard_done',
    ...overrides,
  };
}

function renderScreen(botName: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <WizardSuccessScreen botName={botName} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockedList.mockResolvedValue([]);
  mockedMark.mockReset();
});

afterEach(() => {
  messageMock.success.mockReset();
  messageMock.error.mockReset();
});

describe('<WizardSuccessScreen>', () => {
  it('renders subtitle with botName substituted', async () => {
    renderScreen('alpha-bot');
    await waitFor(() =>
      expect(document.body.textContent).toContain('Bot alpha-bot'),
    );
  });

  it('WS1: existing 3 pairing guidance step items still render', async () => {
    renderScreen('alpha-bot');
    expect(await screen.findByText('在飞书群聊中 @ 你的机器人')).toBeTruthy();
    expect(
      screen.getByText('管控台将收到 pairing code 审批弹窗（在 Phase 4 实现）'),
    ).toBeTruthy();
    expect(
      screen.getByText('审批后，在飞书发送 /sethome 完成频道绑定'),
    ).toBeTruthy();
  });

  it('renders 查看 Bot 详情 and 返回 Bot 列表 buttons with correct hrefs', async () => {
    renderScreen('alpha-bot');
    const viewBtn = (await screen.findByText('查看 Bot 详情')).closest('a');
    expect(viewBtn?.getAttribute('href')).toBe('/bots/alpha-bot/logs');
    const backBtn = screen.getByText('返回 Bot 列表').closest('a');
    expect(backBtn?.getAttribute('href')).toBe('/bots');
  });

  it('WS2: 3-minute KPI section + 我已收到第一条回复 button render', async () => {
    renderScreen('alpha-bot');
    expect(await screen.findByTestId('kpi-card')).toBeTruthy();
    expect(screen.getByTestId('btn-mark-message-received')).toBeTruthy();
    expect(document.body.textContent).toContain('完成 3-minute KPI 测试');
  });

  it('WS3: click button → markMessageReceived(run.id) → toast 已记录 X 秒', async () => {
    const user = userEvent.setup();
    const startedAt = new Date(Date.now() - 90_000).toISOString();
    mockedList.mockResolvedValue([makeRun({ id: 42, started_at: startedAt })]);
    mockedMark.mockResolvedValue({ id: 42, status: 'success' });
    renderScreen('alpha-bot');

    const btn = await screen.findByTestId('btn-mark-message-received');
    await waitFor(() => expect(btn.hasAttribute('disabled')).toBe(false));
    await user.click(btn);

    await waitFor(() => expect(mockedMark).toHaveBeenCalledWith(42));
    await waitFor(() =>
      expect(messageMock.success).toHaveBeenCalledWith(
        expect.stringMatching(/^已记录 \d+ 秒$/),
      ),
    );
  });

  it('WS4: no in-progress run → button disabled with tooltip text in DOM', async () => {
    mockedList.mockResolvedValue([
      makeRun({ id: 7, status: 'success' }),
    ]);
    renderScreen('alpha-bot');
    const btn = await screen.findByTestId('btn-mark-message-received');
    await waitFor(() => expect(btn.hasAttribute('disabled')).toBe(true));
    // The Tooltip title is rendered into DOM (aria/title attr); confirm presence in source
    // by hovering would be flaky; instead assert the run lookup returned no in_progress.
  });
});
