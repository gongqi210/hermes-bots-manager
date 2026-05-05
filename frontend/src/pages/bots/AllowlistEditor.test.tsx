import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AllowlistEditor from './AllowlistEditor';

vi.mock('@/api/gateway', () => ({
  getAllowlist: vi.fn(),
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

import { getAllowlist, putAllowlist } from '@/api/gateway';

const mockedGet = vi.mocked(getAllowlist);
const mockedPut = vi.mocked(putAllowlist);

function renderEditor(users: string[] = []) {
  mockedGet.mockResolvedValue({ bot_name: 'foo', users });
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <AllowlistEditor botName="foo" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockedGet.mockReset();
  mockedPut.mockReset();
});

afterEach(() => {
  messageMock.success.mockReset();
  messageMock.error.mockReset();
});

describe('<AllowlistEditor>', () => {
  it('A1: empty users → red banner + 添加用户 button', async () => {
    renderEditor([]);
    expect(
      await screen.findByTestId('allowlist-empty-banner'),
    ).toBeTruthy();
    expect(document.body.textContent).toContain('当前默认拒绝所有用户');
    expect(screen.getByTestId('btn-add-users')).toBeTruthy();
  });

  it('A2: click 添加用户 → textarea + helper + 保存 button', async () => {
    const user = userEvent.setup();
    renderEditor([]);
    await user.click(await screen.findByTestId('btn-add-users'));
    expect(screen.getByTestId('allowlist-textarea')).toBeTruthy();
    expect(document.body.textContent).toContain('OpenID 在飞书开放平台获取');
    expect(screen.getByTestId('btn-save-allowlist')).toBeTruthy();
  });

  it('A3: save dedupes (ou_a, ou_a, ou_b) → putAllowlist [ou_a, ou_b]', async () => {
    const user = userEvent.setup();
    mockedPut.mockResolvedValue({ bot_name: 'foo', users: ['ou_a', 'ou_b'] });
    renderEditor([]);
    await user.click(await screen.findByTestId('btn-add-users'));
    const ta = screen.getByTestId('allowlist-textarea');
    await user.type(ta, 'ou_a,ou_a,ou_b');
    await user.click(screen.getByTestId('btn-save-allowlist'));
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('foo', ['ou_a', 'ou_b']),
    );
  });

  it('A4: entries without ou_ prefix → warning Alert (not blocking)', async () => {
    const user = userEvent.setup();
    renderEditor([]);
    await user.click(await screen.findByTestId('btn-add-users'));
    const ta = screen.getByTestId('allowlist-textarea');
    await user.type(ta, 'bad_id');
    await waitFor(() =>
      expect(screen.getByTestId('allowlist-warning')).toBeTruthy(),
    );
    // Save still enabled
    expect(
      screen
        .getByTestId('btn-save-allowlist')
        .hasAttribute('disabled'),
    ).toBe(false);
  });

  it('A5: after successful save, restart-hint Alert is shown', async () => {
    const user = userEvent.setup();
    mockedPut.mockResolvedValue({ bot_name: 'foo', users: ['ou_a'] });
    renderEditor([]);
    await user.click(await screen.findByTestId('btn-add-users'));
    await user.type(screen.getByTestId('allowlist-textarea'), 'ou_a');
    await user.click(screen.getByTestId('btn-save-allowlist'));
    await waitFor(() =>
      expect(screen.getByTestId('allowlist-restart-hint')).toBeTruthy(),
    );
    expect(document.body.textContent).toContain(
      '新连接立即生效，已建立的会话需重启 Gateway 才生效',
    );
  });

  it('A6: 422 error → error toast with backend detail', async () => {
    const user = userEvent.setup();
    mockedPut.mockRejectedValueOnce({
      response: { status: 422, data: { detail: 'OpenID 中包含非法字符' } },
    });
    renderEditor([]);
    await user.click(await screen.findByTestId('btn-add-users'));
    await user.type(screen.getByTestId('allowlist-textarea'), 'ou_x');
    await user.click(screen.getByTestId('btn-save-allowlist'));
    await waitFor(() =>
      expect(messageMock.error).toHaveBeenCalledWith('OpenID 中包含非法字符'),
    );
  });
});
