import { describe, expect, it } from 'vitest';
import type { BotStatus } from '@/api/types';
import { statusToColor, statusToLabel } from './statusUtils';

describe('statusToColor', () => {
  it('returns AntD success green for "green"', () => {
    expect(statusToColor('green')).toBe('#52c41a');
  });

  it('returns AntD warning gold for "yellow"', () => {
    expect(statusToColor('yellow')).toBe('#faad14');
  });

  it('returns AntD error red for "red"', () => {
    expect(statusToColor('red')).toBe('#ff4d4f');
  });

  it('returns disabled grey for "grey"', () => {
    expect(statusToColor('grey')).toBe('#bfbfbf');
  });

  it('falls back to grey for unexpected status values', () => {
    // Cast to bypass the union; defensive default-case coverage.
    expect(statusToColor('purple' as unknown as BotStatus)).toBe('#bfbfbf');
  });
});

describe('statusToLabel', () => {
  it('returns Chinese label for each known status', () => {
    expect(statusToLabel('green')).toBe('运行中');
    expect(statusToLabel('yellow')).toBe('启动中');
    expect(statusToLabel('red')).toBe('异常');
    expect(statusToLabel('grey')).toBe('未运行');
  });

  it('falls back to "未运行" for unexpected status values', () => {
    expect(statusToLabel('rainbow' as unknown as BotStatus)).toBe('未运行');
  });
});
