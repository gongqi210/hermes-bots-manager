import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntdApp } from 'antd';

vi.mock('@/hooks/useBots', () => ({
  useCloneBot: vi.fn(),
}));
import { useCloneBot } from '@/hooks/useBots';
import type { UseMutationResult } from '@tanstack/react-query';
import type { BotCloneIn, BotOut } from '@/api/types';
import CloneBotModal from './CloneBotModal';

const mockedUseCloneBot = vi.mocked(useCloneBot);

function makeSourceBot(name = 'source-bot'): BotOut {
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

function makeMutation(overrides: Partial<UseMutationResult<BotOut, Error, BotCloneIn>> = {}) {
  return {
    mutateAsync: vi.fn().mockResolvedValue({ id: 2, name: 'clone-bot' } as BotOut),
    isPending: false,
    isError: false,
    ...overrides,
  } as unknown as UseMutationResult<BotOut, Error, BotCloneIn>;
}

function renderModal(props: { sourceBot: BotOut; open: boolean; onClose: () => void }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <AntdApp>
          <CloneBotModal {...props} />
        </AntdApp>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/** Find the submit button inside the modal (AntD renders text as "克 隆" with spaces). */
function getSubmitButton() {
  const buttons = document.querySelectorAll('button');
  for (const btn of buttons) {
    const text = btn.textContent?.replace(/\s+/g, '');
    if (text === '克隆') return btn;
  }
  throw new Error('Submit button "克隆" not found');
}

beforeEach(() => {
  mockedUseCloneBot.mockReturnValue(makeMutation());
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('<CloneBotModal>', () => {
  it('renders form when open=true with source bot name shown', async () => {
    renderModal({ sourceBot: makeSourceBot('my-source'), open: true, onClose: vi.fn() });
    await waitFor(() => expect(screen.getByText('克隆 Bot')).toBeTruthy());
    // Source bot name should be shown read-only
    expect(screen.getByText('my-source')).toBeTruthy();
    expect(screen.getByText('新 Bot 名称')).toBeTruthy();
  });

  it('validates new_name: rejects "default", uppercase, accepts good name', async () => {
    const mutateAsync = vi.fn().mockResolvedValue({ id: 2, name: 'valid-clone' } as BotOut);
    mockedUseCloneBot.mockReturnValue(makeMutation({ mutateAsync }));

    const user = userEvent.setup();
    renderModal({ sourceBot: makeSourceBot(), open: true, onClose: vi.fn() });
    await waitFor(() => expect(screen.getByText('克隆 Bot')).toBeTruthy());

    const input = screen.getByPlaceholderText('new-bot-name');

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
    await user.type(input, 'valid-clone');
    await user.click(getSubmitButton());
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({ new_name: 'valid-clone' }));
  });

  it('submitting calls useCloneBot.mutateAsync with { new_name }', async () => {
    const mutateAsync = vi.fn().mockResolvedValue({ id: 2, name: 'clone-result' } as BotOut);
    mockedUseCloneBot.mockReturnValue(makeMutation({ mutateAsync }));

    const user = userEvent.setup();
    const onClose = vi.fn();
    renderModal({ sourceBot: makeSourceBot('source-bot'), open: true, onClose });
    await waitFor(() => expect(screen.getByText('克隆 Bot')).toBeTruthy());

    const input = screen.getByPlaceholderText('new-bot-name');
    await user.type(input, 'clone-result');
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({ new_name: 'clone-result' });
      expect(onClose).toHaveBeenCalled();
    });
  });

  it('uses extractErrorMessage on error (W5)', async () => {
    const mutateAsync = vi
      .fn()
      .mockRejectedValue({ response: { data: { detail: 'clone 失败' } } });
    mockedUseCloneBot.mockReturnValue(makeMutation({ mutateAsync }));

    const user = userEvent.setup();
    renderModal({ sourceBot: makeSourceBot(), open: true, onClose: vi.fn() });
    await waitFor(() => expect(screen.getByText('克隆 Bot')).toBeTruthy());

    const input = screen.getByPlaceholderText('new-bot-name');
    await user.type(input, 'good-name');
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(document.body.textContent).toContain('clone 失败');
    });
  });
});
