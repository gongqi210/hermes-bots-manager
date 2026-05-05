// Phase 4-07: 5-state Gateway status pill (D-17/D-18 vocabulary).
// Reusable for the Bot detail Gateway tab; can also be embedded by BotCard later.

import { Badge, Tooltip, Typography } from 'antd';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import 'dayjs/locale/zh-cn';
import type { GatewayState, GatewayStatusOut } from '@/api/types';
import { zhCN } from '@/i18n/zh-CN';

dayjs.extend(relativeTime);
dayjs.locale('zh-cn');

const COLOR_MAP: Record<
  GatewayState,
  'success' | 'warning' | 'error' | 'default'
> = {
  running: 'success',
  starting: 'warning',
  error: 'error',
  stopped: 'default',
  unconfigured: 'default',
};

const LABEL_MAP: Record<GatewayState, string> = {
  running: zhCN.gateway.stateRunning,
  starting: zhCN.gateway.stateStarting,
  stopped: zhCN.gateway.stateStopped,
  error: zhCN.gateway.stateError,
  unconfigured: zhCN.gateway.stateUnconfigured,
};

export function GatewayStatusPill({ status }: { status: GatewayStatusOut }) {
  const tip = (
    <div>
      {status.why && <div>{status.why}</div>}
      {status.last_state_changed_at && (
        <div style={{ marginTop: 4, fontSize: 12, opacity: 0.8 }}>
          {zhCN.gateway.lastChanged}：
          {dayjs(status.last_state_changed_at).fromNow()}
        </div>
      )}
      {status.active_profile && (
        <div style={{ fontSize: 12, opacity: 0.8 }}>
          {zhCN.gateway.activeProfile}：{status.active_profile}
        </div>
      )}
    </div>
  );
  return (
    <Tooltip title={tip}>
      <span data-testid="gateway-status-pill">
        <Badge
          status={COLOR_MAP[status.state]}
          text={LABEL_MAP[status.state]}
        />
        {status.last_state_changed_at && (
          <Typography.Text
            type="secondary"
            style={{ marginLeft: 8, fontSize: 12 }}
          >
            {dayjs(status.last_state_changed_at).fromNow()}
          </Typography.Text>
        )}
      </span>
    </Tooltip>
  );
}
