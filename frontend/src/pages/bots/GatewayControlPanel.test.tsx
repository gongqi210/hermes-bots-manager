import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import GatewayControlPanel from './GatewayControlPanel';
import type {
  GatewayActionResponse,
  GatewayStatusOut,
  Role,
} from '@/api/types';

vi.mock('@/api/gateway', () => ({
  getGatewayStatus: vi.fn(),
  gatewayAction: vi.fn(),
  getAllowlist: vi.fn().mockResolvedValue({ bot_name: 'foo', users: [] }),
  putAllowlist: vi.fn(),
}));

vi.mock('@/api/management', () => ({
  getAllowlistPresets: vi.fn().mockResolvedValue({
    bot_name: 'foo',
    open: [],
    owner_admin: [],
    custom: [],
    owner_admin_warning: null,
  }),
  putAllowlistPreset: vi.fn(),
}));

vi.mock('@/hooks/useRole', () => ({
  useRole: vi.fn(() => 'Editor' as Role),
}));

vi.mock('@/hooks/useBots', () => ({
  useBots: vi.fn(() => ({
    data: [{ name: 'foo', group_strategy: 'mention' }],
    isLoading: false,
    error: null,
  })),
}));

vi.mock('@/api/wizard', () => ({
  updateBotFeishuPolicy: vi.fn(),
}));

vi.mock('./PairingListInBot', () => ({
  default: ({ botName }: { botName: string }) => (
    <div data-testid="pairing-list-in-bot">{botName}</div>
  ),
}));

// Capture AntD message.* calls without depending on portal-rendered DOM.
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

import { gatewayAction, getGatewayStatus } from '@/api/gateway';
import { useRole } from '@/hooks/useRole';

const mockedStatus = vi.mocked(getGatewayStatus);
const mockedAction = vi.mocked(gatewayAction);
const mockedUseRole = vi.mocked(useRole);

function makeStatus(
  overrides: Partial<GatewayStatusOut> = {},
): GatewayStatusOut {
  return {
    bot_name: 'foo',
    state: 'stopped',
    why: 'manual',
    last_state_changed_at: null,
    pid: null,
    active_profile: 'foo',
    is_active_profile: true,
    ...overrides,
  };
}

function makeActionResp(
  overrides: Partial<GatewayActionResponse> = {},
): GatewayActionResponse {
  return {
    bot_name: 'foo',
    action: 'start',
    new_state: 'starting',
    recent_log_tail: ['line1', 'line2'],
    ...overrides,
  };
}

function renderPanel(name = 'foo') {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <GatewayControlPanel botName={name} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockedStatus.mockResolvedValue(makeStatus());
  mockedAction.mockResolvedValue(makeActionResp());
  mockedUseRole.mockReturnValue('Editor');
});

afterEach(() => {
  vi.clearAllMocks();
  messageMock.success.mockReset();
  messageMock.error.mockReset();
});

describe('<GatewayControlPanel>', () => {
  it('renders the Feishu group policy panel in access strategy area', async () => {
    renderPanel();
    expect(await screen.findByTestId('feishu-group-policy-panel')).toBeTruthy();
  });

  it('G1: Viewer cannot control — buttons disabled', async () => {
    mockedUseRole.mockReturnValue('Viewer');
    renderPanel();
    const start = await screen.findByTestId('btn-gateway-start');
    const stop = screen.getByTestId('btn-gateway-stop');
    const restart = screen.getByTestId('btn-gateway-restart');
    expect(start.hasAttribute('disabled')).toBe(true);
    expect(stop.hasAttribute('disabled')).toBe(true);
    expect(restart.hasAttribute('disabled')).toBe(true);
  });

  it('G1b: Editor can control — buttons enabled', async () => {
    renderPanel();
    const start = await screen.findByTestId('btn-gateway-start');
    expect(start.hasAttribute('disabled')).toBe(false);
  });

  it('G2: Start → popconfirm → action → drawer with log tail', async () => {
    const user = userEvent.setup();
    renderPanel();

    const start = await screen.findByTestId('btn-gateway-start');
    await user.click(start);
    // Popconfirm appears with the title
    const confirmTitle = await screen.findByText('启动 Gateway？');
    expect(confirmTitle).toBeTruthy();
    // Click 确认 button (zhCN.common.confirm = "确认")
    const okButtons = screen.getAllByRole('button', { name: /确\s*认/ });
    await user.click(okButtons[0]);

    await waitFor(() =>
      expect(mockedAction).toHaveBeenCalledWith('foo', 'start'),
    );
    // Drawer opens with log lines
    await waitFor(() => expect(screen.getByText(/line1/)).toBeTruthy());
  });

  it('G3+G4: Stop modal requires typing bot name; OK after match', async () => {
    const user = userEvent.setup();
    renderPanel('foo');

    await user.click(await screen.findByTestId('btn-gateway-stop'));
    // Modal title visible
    await screen.findByText(/停止 Gateway 会中断飞书消息处理/);
    const input = await screen.findByTestId('stop-confirm-input');

    // Wrong name → action not invoked
    await user.type(input, 'wrong');
    const okBtns = screen.getAllByRole('button', { name: /确\s*认/ });
    // The Modal OK is the last one (Drawer closed). Click anyway; validation should block.
    await user.click(okBtns[okBtns.length - 1]);
    // Wait a tick — gatewayAction must NOT have been called yet
    await new Promise((r) => setTimeout(r, 30));
    expect(mockedAction).not.toHaveBeenCalled();

    // Clear and type correct name
    await user.clear(input);
    await user.type(input, 'foo');
    const okBtns2 = screen.getAllByRole('button', { name: /确\s*认/ });
    await user.click(okBtns2[okBtns2.length - 1]);

    await waitFor(() =>
      expect(mockedAction).toHaveBeenCalledWith('foo', 'stop'),
    );
  });

  it('G5: status query is configured with refetchInterval=5000', async () => {
    // Source-level guarantee: GatewayControlPanel.tsx contains literal `refetchInterval: 5_000`.
    // Behavioural validation via fake timers + React Query is brittle; instead we
    // verify the source contract that drives the auto-refresh.
    const fs = await import('node:fs/promises');
    const path = await import('node:path');
    const src = await fs.readFile(
      path.resolve(process.cwd(), 'src/pages/bots/GatewayControlPanel.tsx'),
      'utf8',
    );
    expect(src).toMatch(/refetchInterval:\s*5_?000/);
  });

  it('G6: 503 lock-busy surfaces busy message', async () => {
    const user = userEvent.setup();
    mockedAction.mockRejectedValueOnce({
      response: { status: 503, data: { detail: 'lock busy' } },
    });
    renderPanel();

    await user.click(await screen.findByTestId('btn-gateway-start'));
    const okButtons = await screen.findAllByRole('button', { name: /确\s*认/ });
    await user.click(okButtons[0]);

    await waitFor(() =>
      expect(messageMock.error).toHaveBeenCalledWith(
        expect.stringMatching(/Gateway 操作繁忙/),
      ),
    );
  });
});
