import { useState } from 'react';
import { Button, Card, Checkbox, Form, Input, Typography, message } from 'antd';
import { useNavigate } from 'react-router-dom';
import { bootstrap, login } from '@/api/auth';
import { useAuth } from '@/stores/auth';
import { zhCN } from '@/i18n/zh-CN';
import type { AxiosError } from 'axios';

interface FormValues {
  username: string;
  password: string;
  firstInstall: boolean;
}

export default function LoginPage() {
  const nav = useNavigate();
  const setAuth = useAuth((s) => s.setAuth);
  const [loading, setLoading] = useState(false);
  const [firstInstall, setFirstInstall] = useState(false);

  const handleSubmit = async (values: FormValues) => {
    setLoading(true);
    try {
      const resp = values.firstInstall
        ? await bootstrap(values.username, values.password)
        : await login(values.username, values.password);
      setAuth({ user: resp.user, tokens: resp.tokens });
      nav('/bots', { replace: true });
    } catch (err) {
      const ax = err as AxiosError<{ detail: string }>;
      const detail = ax.response?.data?.detail;
      if (ax.response?.status === 401) {
        message.error(zhCN.login.error.invalidCredentials);
      } else if (detail) {
        message.error(detail);
      } else {
        message.error(zhCN.login.error.networkError);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#f0f2f5',
      }}
    >
      <Card style={{ width: 400 }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>
            {firstInstall ? zhCN.login.firstInstallTitle : zhCN.login.title}
          </Typography.Title>
          <Typography.Text type="secondary">
            {firstInstall ? zhCN.login.firstInstallSubtitle : zhCN.login.subtitle}
          </Typography.Text>
        </div>
        <Form<FormValues>
          layout="vertical"
          onFinish={handleSubmit}
          initialValues={{ firstInstall: false }}
        >
          <Form.Item
            name="username"
            rules={[{ required: true, message: zhCN.login.usernamePlaceholder }]}
          >
            <Input
              size="large"
              placeholder={zhCN.login.usernamePlaceholder}
              autoComplete="username"
            />
          </Form.Item>
          <Form.Item
            name="password"
            rules={[{ required: true, message: zhCN.login.passwordPlaceholder }]}
          >
            <Input.Password
              size="large"
              placeholder={zhCN.login.passwordPlaceholder}
              autoComplete="current-password"
            />
          </Form.Item>
          <Form.Item name="firstInstall" valuePropName="checked">
            <Checkbox onChange={(e) => setFirstInstall(e.target.checked)}>
              {zhCN.login.firstInstallTitle}
            </Checkbox>
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large" loading={loading}>
            {firstInstall ? zhCN.login.firstInstallSubmit : zhCN.login.submit}
          </Button>
        </Form>
      </Card>
    </div>
  );
}
