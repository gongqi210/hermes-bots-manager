import { zhCN } from '@/i18n/zh-CN';

export function formatTtl(secs: number): string {
  if (secs <= 0) return zhCN.pairing.expired;
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
