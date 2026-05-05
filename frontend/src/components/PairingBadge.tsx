// Phase 4-09: Bell + count badge in AppLayout header (Owner/Admin only).
// Polls /pairings every 5s; raises an antd notification when the pending count
// increases (SC4 — surface "你曾@过我" pairings even if user wasn't on the page).

import { useEffect, useRef } from 'react';
import { BellOutlined } from '@ant-design/icons';
import { Badge, Button, notification } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { listPairings } from '@/api/pairings';
import { useRole } from '@/hooks/useRole';
import { zhCN } from '@/i18n/zh-CN';

export default function PairingBadge() {
  const role = useRole();
  const nav = useNavigate();
  const enabled = role === 'Owner' || role === 'Admin';

  const { data = [] } = useQuery({
    queryKey: ['pairings'],
    queryFn: () => listPairings(),
    refetchInterval: 5_000,
    enabled,
  });

  const lastCount = useRef(0);
  useEffect(() => {
    if (!enabled) return;
    if (data.length > lastCount.current) {
      notification.info({
        message: zhCN.pairing.notificationTitle,
        description: zhCN.pairing.pendingBadge.replace('{count}', String(data.length)),
        placement: 'topRight',
        onClick: () => nav('/pairings'),
      });
    }
    lastCount.current = data.length;
  }, [data.length, enabled, nav]);

  if (!enabled) return null;

  return (
    <Badge count={data.length} offset={[-2, 4]} data-testid="pairing-badge">
      <Button
        type="text"
        icon={<BellOutlined />}
        onClick={() => nav('/pairings')}
        aria-label={zhCN.pairing.navTitle}
        data-testid="pairing-badge-button"
      />
    </Badge>
  );
}
