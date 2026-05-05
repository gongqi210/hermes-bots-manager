import { Layout, Menu, Button, Typography, Space } from 'antd';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '@/stores/auth';
import { useRole } from '@/hooks/useRole';
import { logout as apiLogout } from '@/api/auth';
import { RoleGuard } from '@/components/RoleGuard';
import PairingBadge from '@/components/PairingBadge';
import { zhCN } from '@/i18n/zh-CN';

const { Header, Sider, Content } = Layout;

export default function AppLayout() {
  const nav = useNavigate();
  const loc = useLocation();
  const user = useAuth((s) => s.user);
  const clear = useAuth((s) => s.clear);
  const role = useRole();

  const handleLogout = async () => {
    try {
      await apiLogout();
    } catch {
      // ignore — clearing local state is authoritative for UX
    }
    clear();
    nav('/login', { replace: true });
  };

  const isOwnerOrAdmin = ['Owner', 'Admin'].includes(role ?? '');
  const menuItems = [
    { key: '/bots', label: zhCN.nav.bots },
    { key: '/pairings', label: zhCN.nav.pairings, disabled: !isOwnerOrAdmin },
    { key: '/audit', label: zhCN.nav.audit, disabled: !isOwnerOrAdmin },
    { key: '/settings', label: zhCN.nav.settings },
  ];

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={220} style={{ background: '#001529' }}>
        <div style={{ color: '#fff', padding: 20, fontSize: 16, fontWeight: 600 }}>
          {zhCN.app.title}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[loc.pathname]}
          onClick={({ key }) => nav(key)}
          items={menuItems}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: '#fff',
            padding: '0 24px',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <Typography.Text type="secondary">
            {user ? `${user.username} · ${user.role}` : ''}
          </Typography.Text>
          <Space size="middle">
            <PairingBadge />
            <RoleGuard role="Viewer">
              <Button onClick={handleLogout}>{zhCN.nav.logout}</Button>
            </RoleGuard>
          </Space>
        </Header>
        <Content style={{ background: '#f0f2f5' }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
