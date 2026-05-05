import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntdApp } from 'antd';

vi.mock('@/hooks/useBots', () => ({
  useRenameBot: vi.fn(),
}));
import { useRenameBot } from '@/hooks/useBots';
import type { UseMutationResult } from '@tanstack/react-query';
import type { BotOut, BotRenameIn } from '@/api/types';
import RenameBotModal from './RenameBotModal';

const mockedUseRenameBot = vi.mocked(useRenameBot);

function makeBot(name = 'old-name'): BotOut {
  return {
    id: 1,
    name,
    feishu_app_id: null,
    feishu_app_secret_last4: null,
    model_name: null,
    tags: [],
    skills_count: 0,
    today_message_count: 0,
    last_heartbeat_at: null,
    status: 'grey',
    why: '未运行',
    last_active_at: null,
    created_at: '2026-04-29T00:00:00Z',
  };
}

function makeMutation(overrides: Partial<UseMutationResult<BotOut, Error, BotRenameIn>> = {}) {
  return {
    mutateAsync: vi.fn().mockResolvedValue({ id: 1, name: 'new-name' } as BotOut),
    isPending: false,
    isError: false,
    ...overrides,
  } as unknown as UseMutationResult<BotOut, Error, BotRenameIn>;
}

function renderModal(props: { bot: BotOut; open: boolean; onClose: () => void }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <AntdApp>
          <RenameBotModal {...props} />
        </AntdApp>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/** Find the rename submit button (AntD renders "重命名" with possible spacing). */
function getSubmitButton() {
  const buttons = document.querySelectorAll('button');
  for (const btn of buttons) {
    const text = btn.textContent?.replace(/\s+/g, '');
    if (text === '重命名') return btn;
  }
  throw new Error('Submit button "重命名" not found');
}

beforeEach(() => {
  mockedUseRenameBot.mockReturnValue(makeMutation());
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('<RenameBotModal>', () => {
  it('validates new_name regex: rejects "default", uppercase, accepts "new-name"', async () => {
    const mutateAsync = vi.fn().mockResolvedValue({ id: 1, name: 'new-name' } as BotOut);
    mockedUseRenameBot.mockReturnValue(makeMutation({ mutateAsync }));

    const user = userEvent.setup();
    renderModal({ bot: makeBot(), open: true, onClose: vi.fn() });
    await waitFor(() => expect(screen.getByText('重命名 Bot')).toBeTruthy());

    const input = screen.getByPlaceholderText('new-name');

    // Reject "default"
    await user.type(input, 'default');
    await user.click(getSubmitButton());
    await waitFor(() => expect(screen.queryByText(/不能为/)).toBeTruthy());

    // Reject uppercase
    await user.clear(input);
    await user.type(input, 'BAD-NAME');
    await user.click(getSubmitButton());
    await waitFor(() => expect(screen.queryByText(/Bot 名仅允许/)).toBeTruthy());

    // Accept valid name
    await user.clear(input);
    await user.type(input, 'new-name');
    await user.click(getSubmitButton());
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({ new_name: 'new-name' }));
  });

  it('submitting calls useRenameBot.mutateAsync with correct payload { new_name }', async () => {
    const mutateAsync = vi.fn().mockResolvedValue({ id: 1, name: 'renamed-bot' } as BotOut);
    mockedUseRenameBot.mockReturnValue(makeMutation({ mutateAsync }));

    const user = userEvent.setup();
    const onClose = vi.fn();
    renderModal({ bot: makeBot('old-name'), open: true, onClose });
    await waitFor(() => expect(screen.getByText('重命名 Bot')).toBeTruthy());

    const input = screen.getByPlaceholderText('new-name');
    await user.type(input, 'renamed-bot');
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({ new_name: 'renamed-bot' });
      expect(onClose).toHaveBeenCalled();
    });
  });

  it('current name shown read-only on the modal body', async () => {
    renderModal({ bot: makeBot('current-bot'), open: true, onClose: vi.fn() });
    await waitFor(() => expect(screen.getByText('重命名 Bot')).toBeTruthy());
    // Current name is shown read-only in the form body
    expect(screen.getByText('current-bot')).toBeTruthy();
  });

  it('uses extractErrorMessage on error (W5)', async () => {
    const mutateAsync = vi
      .fn()
      .mockRejectedValue({ response: { data: { detail: '名称已存在' } } });
    mockedUseRenameBot.mockReturnValue(makeMutation({ mutateAsync }));

    const user = userEvent.setup();
    renderModal({ bot: makeBot(), open: true, onClose: vi.fn() });
    await waitFor(() => expect(screen.getByText('重命名 Bot')).toBeTruthy());

    const input = screen.getByPlaceholderText('new-name');
    await user.type(input, 'conflict-name');
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(document.body.textContent).toContain('名称已存在');
    });
  });
});
