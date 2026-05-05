// Phase 2-05: BotStatus → display color/label mapping.
// Hex codes match AntD 5 default Token (success/warning/error/disabled).

import type { BotStatus } from '@/api/types';

export function statusToColor(status: BotStatus): string {
  switch (status) {
    case 'green':
      return '#52c41a';
    case 'yellow':
      return '#faad14';
    case 'red':
      return '#ff4d4f';
    case 'grey':
    default:
      return '#bfbfbf';
  }
}

export function statusToLabel(status: BotStatus): string {
  switch (status) {
    case 'green':
      return '运行中';
    case 'yellow':
      return '启动中';
    case 'red':
      return '异常';
    case 'grey':
    default:
      return '未运行';
  }
}
