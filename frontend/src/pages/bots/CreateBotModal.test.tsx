// 名优先 CreateBotModal 测试 — 弹窗只收名称，不再收 App ID/Secret。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntdApp } from 'antd';

vi.mock('@/hooks/useBots', () => ({
  useCreateBot: vi.fn(),
}));

import { useCreateBot } from '@/hooks/useBots';
import type { UseMutationResult } from '@tanstack/react-query';
import type { BotCreateIn, BotOut } from '@/api/types';
import CreateBotModal from './CreateBotModal';

const mockedUseCreateBot = vi.mocked(useCreateBot);

function makeMutation(
  overrides: Partial<UseMutationResult<BotOut, Error, BotCreateIn>> = {},
) {
  return {
    mutateAsync: vi.fn().mockResolvedValue({ id: 1, name: 'new-bot' } as BotOut),
    isPending: false,
    isError: false,
    ...overrides,
  } as unknown as UseMutationResult<BotOut, Error, BotCreateIn>;
}

function renderModal(props: { open: boolean; onClose: () => void }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={['/']}>
      <QueryClientProvider client={qc}>
        <AntdApp>
          <Routes>
            <Route path="/" element={<CreateBotModal {...props} />} />
            <Route
              path="/bots/:name/setup"
              element={<div data-testid="setup-route">setup-page</div>}
            />
          </Routes>
        </AntdApp>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

function getSubmitButton() {
  const buttons = document.querySelectorAll('button');
  for (const btn of buttons) {
    const text = btn.textContent?.replace(/\s+/g, '');
    if (text === '创建并进入向导') return btn;
  }
  throw new Error('Submit button "创建并进入向导" not found');
}

beforeEach(() => {
  mockedUseCreateBot.mockReturnValue(makeMutation());
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('<CreateBotModal> (name-first)', () => {
  it('只渲染名称字段，不再展示 App ID / Secret', async () => {
    renderModal({ open: true, onClose: vi.fn() });
    await waitFor(() => expect(screen.getByText('新建 Bot 向导')).toBeTruthy());
    expect(screen.getByText('Bot 名称')).toBeTruthy();
    expect(screen.queryByText('飞书 App ID')).toBeNull();
    expect(screen.queryByText('飞书 App Secret')).toBeNull();
    expect(screen.queryByText('飞书域')).toBeNull();
  });

  it('拒绝包含大写字母的非法名', async () => {
    const user = userEvent.setup();
    renderModal({ open: true, onClose: vi.fn() });
    await waitFor(() => expect(screen.getByText('新建 Bot 向导')).toBeTruthy());
    const nameInput = screen.getByPlaceholderText('my-bot');
    await user.clear(nameInput);
    await user.type(nameInput, 'Test-Bot');
    await user.click(getSubmitButton());
    await waitFor(() => {
      expect(screen.queryByText(/Bot 名仅允许/)).toBeTruthy();
    });
  });

  it('提交时携带默认 wizard 字段且不带任何凭证', async () => {
    const mutateAsync = vi.fn().mockResolvedValue({ id: 1, name: 'submit-bot' });
    mockedUseCreateBot.mockReturnValue(makeMutation({ mutateAsync }));

    const user = userEvent.setup();
    renderModal({ open: true, onClose: vi.fn() });
    await waitFor(() => expect(screen.getByText('新建 Bot 向导')).toBeTruthy());
    await user.type(screen.getByPlaceholderText('my-bot'), 'submit-bot');
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'submit-bot',
          feishu_app_id: null,
          feishu_app_secret: null,
          tags: [],
          domain: 'feishu',
          connection_mode: 'websocket',
          group_strategy: 'mention',
        }),
      );
    });
  });

  it('成功后跳转 /bots/{name}/setup 且关闭弹窗', async () => {
    const mutateAsync = vi.fn().mockResolvedValue({ id: 1, name: 'nav-bot' });
    mockedUseCreateBot.mockReturnValue(makeMutation({ mutateAsync }));

    const user = userEvent.setup();
    const onClose = vi.fn();
    renderModal({ open: true, onClose });
    await waitFor(() => expect(screen.getByText('新建 Bot 向导')).toBeTruthy());
    await user.type(screen.getByPlaceholderText('my-bot'), 'nav-bot');
    await user.click(getSubmitButton());
    await waitFor(() => {
      expect(screen.queryByTestId('setup-route')).toBeTruthy();
    });
    expect(onClose).toHaveBeenCalled();
  });
});
