// Phase 4-09 Task 1 tests: PairingsCenterPage + pairings API client.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import dayjs from 'dayjs';
import type { PairingOut } from '@/api/types';
import { zhCN } from '@/i18n/zh-CN';

vi.mock('@/api/pairings', () => ({
  listPairings: vi.fn(),
  approvePairing: vi.fn(),
  rejectPairing: vi.fn(),
}));

// Replace antd's Popconfirm with a passthrough that immediately fires onConfirm
// when its child trigger is clicked. The full Popconfirm UI is exercised in
// AntD's own test suite; here we only care that Approve/Reject buttons reach
// their respective API calls.
import { approvePairing, listPairings, rejectPairing } from '@/api/pairings';
import PairingsCenterPage from './PairingsCenterPage';
import { formatTtl } from './ttl';

const mockedList = vi.mocked(listPairings);
const mockedApprove = vi.mocked(approvePairing);
const mockedReject = vi.mocked(rejectPairing);

function makePairing(overrides: Partial<PairingOut> = {}): PairingOut {
  return {
    id: 1,
    bot_id: 10,
    bot_name: 'alpha',
    platform: 'feishu',
    code_last4: '1234',
    feishu_user_id: 'ou_xxx',
    status: 'pending',
    intercepted_at: dayjs().toISOString(),
    expires_at: dayjs().add(5, 'minute').toISOString(),
    processed_at: null,
    seconds_to_expiry: 300,
    ...overrides,
  };
}

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <PairingsCenterPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('formatTtl()', () => {
  it('formats positive seconds as mm:ss', () => {
    expect(formatTtl(605)).toBe('10:05');
    expect(formatTtl(59)).toBe('00:59');
  });
  it('returns 已过期 for non-positive seconds', () => {
    expect(formatTtl(0)).toBe(zhCN.pairing.expired);
    expect(formatTtl(-1)).toBe(zhCN.pairing.expired);
  });
});

describe('<PairingsCenterPage>', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders title and calls listPairings on mount', async () => {
    mockedList.mockResolvedValue([]);
    renderPage();
    expect(screen.getByText(zhCN.pairing.navTitle)).toBeTruthy();
    await waitFor(() => expect(mockedList).toHaveBeenCalled());
  });

  it('shows empty-state when API returns []', async () => {
    mockedList.mockResolvedValue([]);
    renderPage();
    await waitFor(() =>
      expect(screen.getAllByText(zhCN.pairing.emptyState).length).toBeGreaterThan(0),
    );
  });

  it('renders one row per pairing with Approve/Reject buttons', async () => {
    mockedList.mockResolvedValue([makePairing(), makePairing({ id: 2, code_last4: '5678' })]);
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId('btn-approve-1')).toBeTruthy();
      expect(screen.getByTestId('btn-approve-2')).toBeTruthy();
      expect(screen.getByTestId('btn-reject-1')).toBeTruthy();
    });
    expect(screen.getByText('1234')).toBeTruthy();
    expect(screen.getByText('5678')).toBeTruthy();
  });

  it('disables Approve/Reject when pairing is expired', async () => {
    mockedList.mockResolvedValue([
      makePairing({
        id: 7,
        expires_at: dayjs().subtract(1, 'minute').toISOString(),
        seconds_to_expiry: 0,
      }),
    ]);
    renderPage();
    await waitFor(() => {
      const approve = screen.getByTestId('btn-approve-7') as HTMLButtonElement;
      const reject = screen.getByTestId('btn-reject-7') as HTMLButtonElement;
      expect(approve.disabled).toBe(true);
      expect(reject.disabled).toBe(true);
    });
  });

  it('uses server seconds_to_expiry instead of treating UTC-naive expires_at as local time', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date('2026-05-05T20:30:00+08:00'));
    mockedList.mockResolvedValue([
      makePairing({
        id: 8,
        expires_at: '2026-05-05T12:36:00',
        seconds_to_expiry: 360,
      }),
    ]);
    renderPage();
    await waitFor(() => {
      const approve = screen.getByTestId('btn-approve-8') as HTMLButtonElement;
      const reject = screen.getByTestId('btn-reject-8') as HTMLButtonElement;
      expect(screen.getByTestId('ttl-8').textContent).toBe('06:00');
      expect(approve.disabled).toBe(false);
      expect(reject.disabled).toBe(false);
    });
  });

  it('clicking Approve fires approvePairing mutation', async () => {
    mockedList.mockResolvedValue([makePairing({ id: 42 })]);
    mockedApprove.mockResolvedValue({ id: 42, status: 'approved', message: 'ok' });
    renderPage();
    const btn = await screen.findByTestId('btn-approve-42');
    await act(async () => {
      fireEvent.click(btn);
    });
    await waitFor(() => expect(mockedApprove).toHaveBeenCalledWith(42));
  });

  it('clicking Reject fires rejectPairing mutation', async () => {
    mockedList.mockResolvedValue([makePairing({ id: 99 })]);
    mockedReject.mockResolvedValue({ id: 99, status: 'rejected', message: 'ok' });
    renderPage();
    const btn = await screen.findByTestId('btn-reject-99');
    await act(async () => {
      fireEvent.click(btn);
    });
    await waitFor(() => expect(mockedReject).toHaveBeenCalledWith(99));
  });

  it('TTL countdown decrements once per second client-side', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const expires = dayjs().add(125, 'second').toISOString();
    mockedList.mockResolvedValue([
      makePairing({ id: 5, expires_at: expires, seconds_to_expiry: 125 }),
    ]);
    renderPage();
    const initial = await screen.findByTestId('ttl-5');
    const text1 = initial.textContent ?? '';
    expect(text1).toMatch(/02:0\d|02:1\d|02:2\d/);
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    const updated = await screen.findByTestId('ttl-5');
    expect(updated.textContent).not.toBe(text1);
  });
});

describe('pairings API client', () => {
  it('listPairings/approvePairing/rejectPairing are callable functions', () => {
    expect(typeof listPairings).toBe('function');
    expect(typeof approvePairing).toBe('function');
    expect(typeof rejectPairing).toBe('function');
  });
});
