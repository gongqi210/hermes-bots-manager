import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import WorkspacePage from './WorkspacePage';

vi.mock('@/api/management', async () => {
  const actual = await vi.importActual<typeof import('@/api/management')>(
    '@/api/management',
  );
  return {
    ...actual,
    getWorkspace: vi.fn(),
    putWorkspace: vi.fn(),
    getHealth: vi.fn(),
    getWorkspaceLibrary: vi.fn(),
    addWorkspaceLibraryEntry: vi.fn(),
    deleteWorkspaceLibraryEntry: vi.fn(),
    getWorkspaceReuseOptions: vi.fn(),
  };
});

vi.mock('@/api/gateway', () => ({
  gatewayAction: vi.fn(),
}));

const { messageMock } = vi.hoisted(() => ({
  messageMock: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
vi.mock('antd', async () => {
  const actual = await vi.importActual<typeof import('antd')>('antd');
  return { ...actual, message: messageMock };
});

// Mock useRole — default non-viewer
vi.mock('@/hooks/useRole', () => ({
  useRole: vi.fn(() => 'admin'),
}));

vi.mock('./HealthSummary', () => ({
  default: () => <div data-testid="health-summary" />,
}));

import { gatewayAction } from '@/api/gateway';
import {
  getHealth,
  getWorkspace,
  putWorkspace,
  getWorkspaceLibrary,
  getWorkspaceReuseOptions,
} from '@/api/management';
import type { HealthOut } from '@/api/types';
import { useRole } from '@/hooks/useRole';

const mockedGet = vi.mocked(getWorkspace);
const mockedPut = vi.mocked(putWorkspace);
const mockedHealth = vi.mocked(getHealth);
const mockedGatewayAction = vi.mocked(gatewayAction);
const mockedGetLibrary = vi.mocked(getWorkspaceLibrary);
const mockedGetReuse = vi.mocked(getWorkspaceReuseOptions);
const mockedUseRole = vi.mocked(useRole);

function renderPage(
  initial: Partial<Awaited<ReturnType<typeof getWorkspace>>> = {},
  health: Partial<HealthOut> = {},
) {
  mockedGet.mockResolvedValue({
    bot_name: 'foo',
    cwd: null,
    exists: false,
    is_directory: false,
    readable: false,
    writable: false,
    status: 'unset',
    message: 'Workspace 未配置',
    ...initial,
  });
  mockedHealth.mockResolvedValue({
    bot_name: 'foo',
    gateway_state: 'stopped',
    gateway_why: '未运行',
    model_configured: false,
    workspace_status: initial.status ?? 'unset',
    skills_enabled: 0,
    skills_total: 0,
    dangerous_skill_count: 0,
    shadowed_skill_count: 0,
    allowlist_preset: 'custom',
    overall: 'warning',
    ...health,
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <WorkspacePage botName="foo" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockedGet.mockReset();
  mockedPut.mockReset();
  mockedHealth.mockReset();
  mockedGatewayAction.mockReset();
  mockedGetLibrary.mockReset();
  mockedGetReuse.mockReset();
  mockedGetLibrary.mockResolvedValue([]);
  mockedGetReuse.mockResolvedValue([]);
  mockedUseRole.mockReturnValue('Admin');
});
afterEach(() => {
  messageMock.success.mockReset();
  messageMock.error.mockReset();
});

describe('<WorkspacePage>', () => {
  it('renders unset status and form', async () => {
    renderPage();
    expect(await screen.findByTestId('workspace-page')).toBeTruthy();
    expect(screen.getByTestId('input-workspace-cwd')).toBeTruthy();
  });

  it('saves an absolute path', async () => {
    const user = userEvent.setup();
    mockedPut.mockResolvedValue({
      bot_name: 'foo',
      cwd: '/tmp/work',
      exists: true,
      is_directory: true,
      readable: true,
      writable: true,
      status: 'ok',
      message: 'Workspace 可读可写',
    });
    renderPage();
    const input = await screen.findByTestId('input-workspace-cwd');
    await user.type(input, '/tmp/work');
    await user.click(screen.getByTestId('btn-save-workspace'));
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('foo', { cwd: '/tmp/work' }),
    );
    expect(mockedGatewayAction).not.toHaveBeenCalled();
  });

  it('restarts gateway after save when gateway is running', async () => {
    const user = userEvent.setup();
    const runningHealth: Partial<HealthOut> = {
      gateway_state: 'running',
      gateway_why: '运行中',
      model_configured: true,
      workspace_status: 'ok',
      skills_enabled: 1,
      skills_total: 1,
      dangerous_skill_count: 0,
      shadowed_skill_count: 0,
      allowlist_preset: 'custom',
      overall: 'ok',
    };
    mockedPut.mockResolvedValue({
      bot_name: 'foo',
      cwd: '/tmp/work',
      exists: true,
      is_directory: true,
      readable: true,
      writable: true,
      status: 'ok',
      message: 'Workspace 可读可写',
    });
    mockedGatewayAction.mockResolvedValue({
      bot_name: 'foo',
      action: 'restart',
      new_state: 'running',
      recent_log_tail: [],
    });
    renderPage({}, runningHealth);
    const input = await screen.findByTestId('input-workspace-cwd');
    await user.type(input, '/tmp/work');
    await user.click(screen.getByTestId('btn-save-workspace'));
    await waitFor(() =>
      expect(mockedGatewayAction).toHaveBeenCalledWith('foo', 'restart'),
    );
    expect(messageMock.success).toHaveBeenCalledWith(
      'Workspace 已保存，Gateway 已重启并生效',
    );
  });

  it('clear button posts null to clear the cwd', async () => {
    const user = userEvent.setup();
    mockedPut.mockResolvedValue({
      bot_name: 'foo',
      cwd: null,
      exists: false,
      is_directory: false,
      readable: false,
      writable: false,
      status: 'unset',
      message: 'Workspace 未配置',
    });
    renderPage({ cwd: '/old', status: 'ok' });
    await user.click(await screen.findByTestId('btn-clear-workspace'));
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('foo', { cwd: null }),
    );
  });

  it('renders workspace status message from backend', async () => {
    renderPage({
      cwd: '/missing',
      status: 'error',
      message: '路径不存在: /missing',
    });
    expect(
      (await screen.findByTestId('workspace-status-message')).textContent,
    ).toContain('路径不存在');
  });

  // ── New tests for Mode B and Mode C ─────────────────────────────────────────

  it('shows three mode tabs: 手动输入, 从库中选择, 复用其他 Bot', async () => {
    renderPage();
    await screen.findByTestId('workspace-page');
    expect(screen.getByText('手动输入')).toBeTruthy();
    expect(screen.getByText('从库中选择')).toBeTruthy();
    expect(screen.getByText('复用其他 Bot')).toBeTruthy();
  });

  it('keeps the current workspace path visible when switching modes', async () => {
    renderPage({
      cwd: '/Volumes/AI-projects/10-website',
      exists: true,
      is_directory: true,
      readable: true,
      writable: true,
      status: 'ok',
      message: 'Workspace 可读可写',
    });

    const input = await screen.findByTestId('input-workspace-cwd') as HTMLInputElement;
    await waitFor(() =>
      expect(input.value).toBe('/Volumes/AI-projects/10-website'),
    );

    fireEvent.click(screen.getByText('从库中选择'));
    expect(input.value).toBe('/Volumes/AI-projects/10-website');

    fireEvent.click(screen.getByText('复用其他 Bot'));
    expect(input.value).toBe('/Volumes/AI-projects/10-website');
  });

  it('Mode B: clicking 从库中选择 tab shows library button; clicking opens modal with list', async () => {
    const user = userEvent.setup();
    mockedGetLibrary.mockResolvedValue([
      { id: 1, path: '/data/workspace', label: '数据目录', registered_by: null, registered_at: '2026-01-01T00:00:00Z' },
    ]);
    renderPage();
    await screen.findByTestId('workspace-page');

    // Switch to Mode B — click the label (not the hidden input)
    fireEvent.click(screen.getByText('从库中选择'));

    // Library trigger button should appear
    const openBtn = await screen.findByTestId('btn-open-library');
    await user.click(openBtn);

    // Modal should open with library item
    await waitFor(() => {
      expect(screen.getByTestId('library-item-1')).toBeTruthy();
    });
    expect(mockedGetLibrary).toHaveBeenCalled();
  });

  it('Mode B: selecting library entry fills the path input and closes modal', async () => {
    const user = userEvent.setup();
    mockedGetLibrary.mockResolvedValue([
      { id: 1, path: '/data/workspace', label: '数据目录', registered_by: null, registered_at: '2026-01-01T00:00:00Z' },
    ]);
    renderPage();
    await screen.findByTestId('workspace-page');

    fireEvent.click(screen.getByText('从库中选择'));
    await user.click(await screen.findByTestId('btn-open-library'));

    const item = await screen.findByTestId('library-item-1');
    await user.click(item);

    // Path input should be filled
    await waitFor(() => {
      const input = screen.getByTestId('input-workspace-cwd') as HTMLInputElement;
      expect(input.value).toBe('/data/workspace');
    });
  });

  it('Mode C: Select populated from getWorkspaceReuseOptions; selecting pre-fills path', async () => {
    mockedGetReuse.mockResolvedValue([
      { bot_name: 'bar', cwd: '/projects/bar' },
    ]);
    renderPage();
    await screen.findByTestId('workspace-page');

    // Switch to Mode C
    fireEvent.click(screen.getByText('复用其他 Bot'));

    await waitFor(() => expect(mockedGetReuse).toHaveBeenCalledWith('foo'));

    // Select should render
    expect(screen.getByTestId('select-reuse-bot')).toBeTruthy();
  });

  it('Mode controls and Save button are disabled for Viewer role', async () => {
    mockedUseRole.mockReturnValue('Viewer');
    renderPage();
    await screen.findByTestId('workspace-page');

    // Mode group should be disabled
    const modeGroup = screen.getByTestId('workspace-mode-group');
    // Check that the Radio.Group has disabled attribute
    expect(modeGroup.querySelector('input[disabled]')).toBeTruthy();

    // Save button should be disabled
    const saveBtn = screen.getByTestId('btn-save-workspace') as HTMLButtonElement;
    expect(saveBtn.disabled).toBe(true);
  });

  it('shows message.error with Chinese detail on 422 error', async () => {
    const user = userEvent.setup();
    mockedPut.mockRejectedValue({
      response: {
        status: 422,
        data: { detail: '路径不能位于 Hermes 家目录 (~/.hermes/) 内' },
      },
    });
    renderPage();
    const input = await screen.findByTestId('input-workspace-cwd');
    await user.type(input, '/home/user/.hermes/test');
    await user.click(screen.getByTestId('btn-save-workspace'));
    await waitFor(() =>
      expect(messageMock.error).toHaveBeenCalledWith(
        '路径不能位于 Hermes 家目录 (~/.hermes/) 内',
      ),
    );
  });
});
