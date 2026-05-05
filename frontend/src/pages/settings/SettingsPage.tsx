import { Typography } from 'antd';
import { zhCN } from '@/i18n/zh-CN';

export default function SettingsPage() {
  return (
    <div style={{ padding: 24 }}>
      <Typography.Title level={3}>{zhCN.nav.settings}</Typography.Title>
      <Typography.Paragraph type="secondary">{zhCN.settings.placeholder}</Typography.Paragraph>
    </div>
  );
}
