import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntdApp } from 'antd';

vi.mock('@/hooks/useBots', () => ({
  useDeleteBot: vi.fn(),
}));
import { useDeleteBot } from '@/hooks/useBots';
import type { UseMutationResult } from '@tanstack/react-query';
import type { BotDeleteIn, BotOut } from '@/api/types';
import DeleteBotModal from './DeleteBotModal';

const mockedUseDeleteBot = vi.mocked(useDeleteBot);

const BOT_FIXTURE: BotOut = {
  id: 1,
  name: 'test-bot',
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

function makeMutation(overrides: Partial<UseMutationResult<void, Error, BotDeleteIn>> = {}) {
  return {
    mutateAsync: vi.fn().mockResolvedValue(undefined),
    isPending: false,
    isError: false,
    ...overrides,
  } as unknown as UseMutationResult<void, Error, BotDeleteIn>;
}

function renderModal(props: { bot: BotOut; open: boolean; onClose: () => void }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <AntdApp>
          <DeleteBotModal {...props} />
        </AntdApp>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/** Find the delete submit button (AntD renders "永久删除" possibly with spacing). */
function getSubmitButton() {
  const buttons = document.querySelectorAll('button');
  for (const btn of buttons) {
    const text = btn.textContent?.replace(/\s+/g, '');
    if (text === '永久删除') return btn;
  }
  throw new Error('Submit button "永久删除" not found');
}

beforeEach(() => {
  mockedUseDeleteBot.mockReturnValue(makeMutation());
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('<DeleteBotModal>', () => {
  it('test_validator_rejects_mismatched_name: typing wrong name shows 名称不一致', async () => {
    const mutateAsync = vi.fn();
    mockedUseDeleteBot.mockReturnValue(makeMutation({ mutateAsync }));

    const user = userEvent.setup();
    renderModal({ bot: BOT_FIXTURE, open: true, onClose: vi.fn() });
    await waitFor(() => expect(screen.getByText('删除 Bot')).toBeTruthy());

    const input = screen.getByTestId('delete-bot-confirm-input');
    await user.type(input, 'wrong');
    await user.click(getSubmitButton());

    await waitFor(() => {
      // Validator should reject with '名称不一致'
      expect(screen.queryByText(/名称不一致/)).toBeTruthy();
    });
    // Mutation should NOT have been called due to validation error
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it('test_validator_accepts_exact_match: typing exact bot name passes validation', async () => {
    const mutateAsync = vi.fn().mockResolvedValue(undefined);
    mockedUseDeleteBot.mockReturnValue(makeMutation({ mutateAsync }));

    const user = userEvent.setup();
    const onClose = vi.fn();
    renderModal({ bot: BOT_FIXTURE, open: true, onClose });
    await waitFor(() => expect(screen.getByText('删除 Bot')).toBeTruthy());

    const input = screen.getByTestId('delete-bot-confirm-input');
    await user.type(input, 'test-bot'); // exact match
    await user.click(getSubmitButton());

    await waitFor(() => {
      // Should succeed — no validation error shown
      expect(screen.queryByText(/名称不一致/)).toBeNull();
      expect(mutateAsync).toHaveBeenCalled();
    });
  });

  it('test_submit_sends_confirm_name_payload: payload contains { confirm_name: "test-bot" }', async () => {
    const mutateAsync = vi.fn().mockResolvedValue(undefined);
    mockedUseDeleteBot.mockReturnValue(makeMutation({ mutateAsync }));

    const user = userEvent.setup();
    const onClose = vi.fn();
    renderModal({ bot: BOT_FIXTURE, open: true, onClose });
    await waitFor(() => expect(screen.getByText('删除 Bot')).toBeTruthy());

    const input = screen.getByTestId('delete-bot-confirm-input');
    await user.type(input, 'test-bot');
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({ confirm_name: 'test-bot' });
    });
  });

  it('W5 bonus test_submission_error_uses_extractErrorMessage: shows response.data.detail', async () => {
    const mutateAsync = vi
      .fn()
      .mockRejectedValue({ response: { data: { detail: '不能删除默认' } } });
    mockedUseDeleteBot.mockReturnValue(makeMutation({ mutateAsync }));

    const user = userEvent.setup();
    renderModal({ bot: BOT_FIXTURE, open: true, onClose: vi.fn() });
    await waitFor(() => expect(screen.getByText('删除 Bot')).toBeTruthy());

    const input = screen.getByTestId('delete-bot-confirm-input');
    await user.type(input, 'test-bot');
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(document.body.textContent).toContain('不能删除默认');
    });
  });

  it('shows warning alert with warning text', async () => {
    renderModal({ bot: BOT_FIXTURE, open: true, onClose: vi.fn() });
    await waitFor(() => expect(screen.getByText('删除 Bot')).toBeTruthy());
    // Warning message should be visible
    expect(screen.getByText(/归档为 tar.gz/)).toBeTruthy();
  });
});
