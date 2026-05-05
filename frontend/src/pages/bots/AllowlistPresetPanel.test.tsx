import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AllowlistPresetPanel from './AllowlistPresetPanel';

vi.mock('@/api/management', () => ({
  getAllowlistPresets: vi.fn(),
  putAllowlistPreset: vi.fn(),
}));

vi.mock('@/api/gateway', () => ({
  getAllowlist: vi.fn().mockResolvedValue({ bot_name: 'foo', users: [] }),
  putAllowlist: vi.fn(),
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

vi.mock('@/hooks/useRole', () => ({ useRole: vi.fn() }));

import { getAllowlistPresets, putAllowlistPreset } from '@/api/management';
import { useRole } from '@/hooks/useRole';

const mockedGet = vi.mocked(getAllowlistPresets);
const mockedPut = vi.mocked(putAllowlistPreset);
const mockedUseRole = vi.mocked(useRole);

function renderPanel(opts?: {
  currentAllowlist?: string[];
  ownerAdmin?: string[];
  warning?: string | null;
  role?: 'Owner' | 'Admin' | 'Editor' | 'Viewer';
}) {
  const role = opts?.role ?? 'Owner';
  mockedUseRole.mockReturnValue(role);
  mockedGet.mockResolvedValue({
    bot_name: 'foo',
    open: [],
    owner_admin: opts?.ownerAdmin ?? ['ou_owner', 'ou_admin'],
    custom: opts?.currentAllowlist ?? [],
    owner_admin_warning: opts?.warning ?? null,
  });
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <AllowlistPresetPanel
        botName="foo"
        currentAllowlist={opts?.currentAllowlist ?? []}
        onSaved={() => {}}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockedGet.mockReset();
  mockedPut.mockReset();
  mockedUseRole.mockReset();
});
afterEach(() => {
  messageMock.success.mockReset();
  messageMock.error.mockReset();
});

describe('<AllowlistPresetPanel>', () => {
  it('P1: renders three radio options', async () => {
    renderPanel();
    expect(await screen.findByTestId('preset-radio-open')).toBeTruthy();
    expect(screen.getByTestId('preset-radio-owner_admin')).toBeTruthy();
    expect(screen.getByTestId('preset-radio-custom')).toBeTruthy();
    expect(document.body.textContent).toContain('开放测试');
    expect(document.body.textContent).toContain('仅 Owner/Admin');
  });

  it('P2: selecting 开放测试 shows red Alert', async () => {
    const user = userEvent.setup();
    renderPanel({ currentAllowlist: ['ou_a'] });
    await user.click(await screen.findByTestId('preset-radio-open'));
    const alert = await screen.findByTestId('preset-open-warning');
    expect(alert).toBeTruthy();
    expect(document.body.textContent).toContain(
      '任意飞书用户均可发消息',
    );
  });

  it('P3: selecting 仅 Owner/Admin shows resolved IDs from preset response', async () => {
    const user = userEvent.setup();
    renderPanel({ ownerAdmin: ['ou_owner', 'ou_admin'] });
    await user.click(await screen.findByTestId('preset-radio-owner_admin'));
    const list = await screen.findByTestId('preset-owner-admin-list');
    expect(list.textContent).toContain('ou_owner');
    expect(list.textContent).toContain('ou_admin');
  });

  it('P4: 保存策略 calls putAllowlistPreset with selected key', async () => {
    const user = userEvent.setup();
    mockedPut.mockResolvedValue({ bot_name: 'foo', users: [] });
    renderPanel();
    await user.click(await screen.findByTestId('preset-radio-open'));
    await user.click(screen.getByTestId('btn-save-preset'));
    await waitFor(() => {
      expect(mockedPut).toHaveBeenCalledWith('foo', 'open');
    });
  });

  it('P5: Editor role disables radios + save button', async () => {
    renderPanel({ role: 'Editor' });
    const save = await screen.findByTestId('btn-save-preset');
    expect((save as HTMLButtonElement).disabled).toBe(true);
    // antd Radio renders input as a sibling; query the whole panel for any input
    const inputs = document
      .querySelector('[data-testid="allowlist-preset-panel"]')!
      .querySelectorAll('input[type="radio"]');
    expect(inputs.length).toBeGreaterThan(0);
    inputs.forEach((i) => {
      expect((i as HTMLInputElement).disabled).toBe(true);
    });
  });

  it('P6: updates selected preset when current allowlist loads after mount', async () => {
    mockedUseRole.mockReturnValue('Owner');
    mockedGet.mockResolvedValue({
      bot_name: 'foo',
      open: [],
      owner_admin: ['ou_owner'],
      custom: ['ou_custom'],
      owner_admin_warning: null,
    });
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = render(
      <QueryClientProvider client={qc}>
        <AllowlistPresetPanel botName="foo" currentAllowlist={[]} />
      </QueryClientProvider>,
    );

    view.rerender(
      <QueryClientProvider client={qc}>
        <AllowlistPresetPanel botName="foo" currentAllowlist={['ou_custom']} />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(
        (document.querySelector('input[value="custom"]') as HTMLInputElement)
          .checked,
      ).toBe(true);
    });
  });
});
