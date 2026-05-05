import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Role } from '@/api/types';
import SkillsPage from './SkillsPage';

vi.mock('@/api/management', async () => {
  const actual = await vi.importActual<typeof import('@/api/management')>(
    '@/api/management',
  );
  return {
    ...actual,
    getSkills: vi.fn(),
    putSkills: vi.fn(),
    getHealth: vi.fn(),
    uploadSkill: vi.fn(),
  };
});

vi.mock('@/hooks/useRole', () => ({
  useRole: vi.fn(() => 'Owner' as Role),
}));

const { messageMock } = vi.hoisted(() => ({
  messageMock: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
vi.mock('antd', async () => {
  const actual = await vi.importActual<typeof import('antd')>('antd');
  return { ...actual, message: messageMock };
});

import { getHealth, getSkills, putSkills, uploadSkill } from '@/api/management';
import { useRole } from '@/hooks/useRole';
const mockedGet = vi.mocked(getSkills);
const mockedPut = vi.mocked(putSkills);
const mockedHealth = vi.mocked(getHealth);
const mockedUpload = vi.mocked(uploadSkill);
const mockedRole = vi.mocked(useRole);

const SAMPLE_SKILLS = [
  {
    name: 'weather',
    category: null,
    description: 'weather skill',
    source: 'profile' as const,
    enabled: true,
    dangerous: false,
    missing_deps: [],
    requires_tools: [],
  },
  {
    name: 'shellrunner',
    category: 'shell',
    description: 'execute shell commands',
    source: 'profile' as const,
    enabled: false,
    dangerous: true,
    missing_deps: [],
    requires_tools: [],
  },
  {
    name: 'shadowed-skill',
    category: null,
    description: 'an overridden skill',
    source: 'profile' as const,
    enabled: true,
    dangerous: false,
    shadowed_source: 'global',
    missing_deps: [],
    requires_tools: [],
  },
  {
    name: 'searchcode',
    category: null,
    description: 'searches code',
    source: 'global' as const,
    enabled: true,
    dangerous: false,
    missing_deps: ['ripgrep'],
    requires_tools: ['ripgrep'],
  },
];

function renderPage() {
  mockedGet.mockResolvedValue({
    bot_name: 'foo',
    skills: SAMPLE_SKILLS,
    disabled: ['shellrunner'],
  });
  mockedHealth.mockResolvedValue({
    bot_name: 'foo',
    gateway_state: 'stopped',
    gateway_why: '未运行',
    model_configured: false,
    workspace_status: 'unset',
    skills_enabled: 3,
    skills_total: 4,
    dangerous_skill_count: 0,
    shadowed_skill_count: 0,
    allowlist_preset: 'custom',
    overall: 'warning',
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SkillsPage botName="foo" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockedGet.mockReset();
  mockedPut.mockReset();
  mockedHealth.mockReset();
  mockedUpload.mockReset();
  mockedRole.mockReturnValue('Owner');
});
afterEach(() => {
  messageMock.success.mockReset();
  messageMock.error.mockReset();
});

describe('<SkillsPage>', () => {
  it('renders skills with dangerous tag', async () => {
    renderPage();
    expect(await screen.findByTestId('switch-skill-weather')).toBeTruthy();
    expect(screen.getByTestId('switch-skill-shellrunner')).toBeTruthy();
    expect(screen.getByTestId('dangerous-tag-shellrunner')).toBeTruthy();
  });

  it('disables a non-dangerous skill without confirmation', async () => {
    const user = userEvent.setup();
    mockedPut.mockResolvedValue({
      bot_name: 'foo',
      skills: SAMPLE_SKILLS.map((s) =>
        s.name === 'weather' ? { ...s, enabled: false } : s,
      ),
      disabled: ['shellrunner', 'weather'],
    });
    renderPage();
    await user.click(await screen.findByTestId('switch-skill-weather'));
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('foo', {
        disabled: expect.arrayContaining(['shellrunner', 'weather']),
      }),
    );
  });

  it('requires bot-name confirmation to enable a dangerous skill', async () => {
    const user = userEvent.setup();
    mockedPut.mockResolvedValue({
      bot_name: 'foo',
      skills: SAMPLE_SKILLS.map((s) =>
        s.name === 'shellrunner' ? { ...s, enabled: true } : s,
      ),
      disabled: [],
    });
    renderPage();
    await user.click(await screen.findByTestId('switch-skill-shellrunner'));
    const input = await screen.findByTestId('input-dangerous-confirm');
    expect(input).toBeTruthy();
    await user.type(input, 'wrong');
    const okBtn = screen.getByTestId('btn-confirm-dangerous');
    expect(okBtn.hasAttribute('disabled')).toBe(true);
    await user.clear(input);
    await user.type(input, 'foo');
    await user.click(okBtn);
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('foo', {
        disabled: [],
        confirm_name: 'foo',
      }),
    );
  });

  it('search filters by description', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('switch-skill-weather');
    const input = screen.getByPlaceholderText('搜索 Skill 名称 / 描述 / 类型');
    await user.type(input, 'weather');
    await waitFor(() => {
      expect(screen.queryByTestId('switch-skill-shellrunner')).toBeNull();
      expect(screen.getByTestId('switch-skill-weather')).toBeTruthy();
    });
  });

  it('shows shadowed tag when shadowed_source is set', async () => {
    renderPage();
    const tag = await screen.findByTestId('shadowed-tag-shadowed-skill');
    expect(tag.textContent).toMatch(/被遮蔽/);
    expect(tag.textContent).toMatch(/global/);
  });

  it('shows missing-deps alert when missing_deps is non-empty', async () => {
    renderPage();
    const alert = await screen.findByTestId('missing-deps-searchcode');
    expect(alert.textContent).toMatch(/ripgrep/);
  });

  it('renders bulk action bar after row selection and disables in bulk', async () => {
    const user = userEvent.setup();
    mockedPut.mockResolvedValue({
      bot_name: 'foo',
      skills: SAMPLE_SKILLS,
      disabled: ['shellrunner', 'weather'],
    });
    renderPage();
    await screen.findByTestId('switch-skill-weather');
    const checkboxes = screen.getAllByRole('checkbox');
    // first checkbox is the header "select all"; pick the row checkbox for weather
    // Find the row by its switch then the row's checkbox
    const weatherRow = screen.getByTestId('switch-skill-weather').closest('tr')!;
    const weatherCheckbox = within(weatherRow).getByRole('checkbox');
    await user.click(weatherCheckbox);

    const bar = await screen.findByTestId('bulk-action-bar');
    expect(bar).toBeTruthy();
    await user.click(screen.getByTestId('btn-bulk-disable'));
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('foo', {
        disabled: expect.arrayContaining(['shellrunner', 'weather']),
      }),
    );
    expect(checkboxes.length).toBeGreaterThan(0);
  });

  it('requires confirmation when bulk-enabling a dangerous skill', async () => {
    const user = userEvent.setup();
    mockedPut.mockResolvedValue({
      bot_name: 'foo',
      skills: SAMPLE_SKILLS.map((s) =>
        s.name === 'shellrunner' ? { ...s, enabled: true } : s,
      ),
      disabled: [],
    });
    renderPage();
    const shellRow = (await screen.findByTestId('switch-skill-shellrunner')).closest('tr')!;
    await user.click(within(shellRow).getByRole('checkbox'));
    await user.click(screen.getByTestId('btn-bulk-enable'));

    const input = await screen.findByTestId('input-dangerous-confirm');
    expect(screen.getByTestId('dangerous-skill-list').textContent).toContain('shellrunner');
    expect(screen.getByTestId('btn-confirm-dangerous').hasAttribute('disabled')).toBe(true);
    await user.type(input, 'foo');
    await user.click(screen.getByTestId('btn-confirm-dangerous'));

    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('foo', {
        disabled: [],
        confirm_name: 'foo',
      }),
    );
  });

  it('hides upload button for non-Owner roles', async () => {
    mockedRole.mockReturnValue('Editor');
    renderPage();
    await screen.findByTestId('switch-skill-weather');
    expect(screen.queryByTestId('btn-upload-skill')).toBeNull();
  });

  it('shows upload button for Owner role', async () => {
    mockedRole.mockReturnValue('Owner');
    renderPage();
    await screen.findByTestId('switch-skill-weather');
    expect(screen.getByTestId('btn-upload-skill')).toBeTruthy();
  });

  it('upload button sends the selected zip through uploadSkill', async () => {
    const user = userEvent.setup();
    mockedUpload.mockResolvedValue({
      name: 'uploaded-skill',
      category: null,
      description: null,
      source: 'uploaded',
      enabled: true,
      dangerous: false,
      missing_deps: [],
      requires_tools: [],
    });
    renderPage();
    await screen.findByTestId('btn-upload-skill');
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['zip-content'], 'uploaded-skill.zip', { type: 'application/zip' });

    await user.upload(input, file);

    await waitFor(() => expect(mockedUpload).toHaveBeenCalledWith('foo', file));
  });

  it('source filter narrows the table to a single source', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('switch-skill-searchcode');
    // default: searchcode (global) is visible
    expect(screen.getByTestId('switch-skill-searchcode')).toBeTruthy();
    // open source select and pick "本 Bot" (profile)
    const sourceSelect = screen.getByTestId('select-source-filter');
    await user.click(within(sourceSelect).getByRole('combobox'));
    // AntD renders options with role=option in a portal; pick the profile one.
    const opt = await screen.findByTitle('本 Bot');
    await user.click(opt);
    await waitFor(() => {
      expect(screen.queryByTestId('switch-skill-searchcode')).toBeNull();
      expect(screen.getByTestId('switch-skill-weather')).toBeTruthy();
    });
  });

  it('status filter narrows the table to disabled skills', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('switch-skill-weather');
    const statusSelect = screen.getByTestId('select-status-filter');
    await user.click(within(statusSelect).getByRole('combobox'));
    const opt = await screen.findByTitle('已禁用');
    await user.click(opt);

    await waitFor(() => {
      expect(screen.getByTestId('switch-skill-shellrunner')).toBeTruthy();
      expect(screen.queryByTestId('switch-skill-weather')).toBeNull();
    });
  });
});
