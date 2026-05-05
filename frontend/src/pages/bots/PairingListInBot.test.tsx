// Phase 4-09 Task 2 tests: PairingListInBot — per-Bot pending list embedded
// in Bot detail Gateway tab.

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import dayjs from 'dayjs';
import type { PairingOut } from '@/api/types';
import { zhCN } from '@/i18n/zh-CN';

vi.mock('@/api/pairings', () => ({
  listPairings: vi.fn(),
  approvePairing: vi.fn(),
  rejectPairing: vi.fn(),
}));

import { approvePairing, listPairings, rejectPairing } from '@/api/pairings';
import PairingListInBot from './PairingListInBot';

const mockedList = vi.mocked(listPairings);
const mockedApprove = vi.mocked(approvePairing);

function makePairing(over: Partial<PairingOut> = {}): PairingOut {
  return {
    id: 1,
    bot_id: 1,
    bot_name: 'alpha',
    platform: 'feishu',
    code_last4: '4321',
    feishu_user_id: 'ou_aaa',
    status: 'pending',
    intercepted_at: dayjs().toISOString(),
    expires_at: dayjs().add(5, 'minute').toISOString(),
    processed_at: null,
    seconds_to_expiry: 300,
    ...over,
  };
}

function renderList(botName = 'alpha') {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <PairingListInBot botName={botName} />
    </QueryClientProvider>,
  );
}

describe('<PairingListInBot>', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    void rejectPairing;
  });

  it('calls listPairings(botName) — per-Bot scoped query', async () => {
    mockedList.mockResolvedValue([]);
    renderList('foo');
    await waitFor(() => expect(mockedList).toHaveBeenCalledWith('foo'));
  });

  it('shows empty-state text when there are no pending pairings', async () => {
    mockedList.mockResolvedValue([]);
    renderList();
    await waitFor(() =>
      expect(screen.getAllByText(zhCN.pairing.emptyState).length).toBeGreaterThan(0),
    );
  });

  it('renders one row per pairing with code_last4 visible', async () => {
    mockedList.mockResolvedValue([
      makePairing({ id: 11, code_last4: 'AAAA' }),
      makePairing({ id: 12, code_last4: 'BBBB' }),
    ]);
    renderList();
    await waitFor(() => {
      expect(screen.getByText('AAAA')).toBeTruthy();
      expect(screen.getByText('BBBB')).toBeTruthy();
    });
  });

  it('clicking Approve fires approvePairing mutation', async () => {
    mockedList.mockResolvedValue([makePairing({ id: 77 })]);
    mockedApprove.mockResolvedValue({ id: 77, status: 'approved', message: 'ok' });
    renderList();
    const btn = await screen.findByTestId('btn-approve-77');
    await act(async () => {
      fireEvent.click(btn);
    });
    await waitFor(() => expect(mockedApprove).toHaveBeenCalledWith(77));
  });
});
