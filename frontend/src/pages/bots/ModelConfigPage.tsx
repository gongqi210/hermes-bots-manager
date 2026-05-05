// Phase 5: Model configuration page (chat tab).
// One-click ChatGPT auth subscription button is the canonical default. The
// form below lets operators tweak the four fields by hand if they want a
// different provider/base_url/model.

import { Alert, Button, Card, Form, Input, Space, Tag, Typography, message } from 'antd';
import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CHATGPT_AUTH_DEFAULT,
  getModelConfig,
  putModelConfig,
} from '@/api/management';
import type { ModelConfigUpdateIn } from '@/api/types';
import { zhCN } from '@/i18n/zh-CN';
import { extractErrorMessage } from '@/utils/errors';
import HealthSummary from './HealthSummary';

interface FormValues {
  provider: string;
  model: string;
  base_url?: string;
  api_mode?: string;
}

export default function ModelConfigPage({ botName }: { botName: string }) {
  const qc = useQueryClient();
  const [form] = Form.useForm<FormValues>();

  const { data, isLoading } = useQuery({
    queryKey: ['model-config', botName],
    queryFn: () => getModelConfig(botName),
  });

  useEffect(() => {
    if (data) {
      form.setFieldsValue({
        provider: data.provider ?? '',
        model: data.model ?? '',
        base_url: data.base_url ?? '',
        api_mode: data.api_mode ?? '',
      });
    }
  }, [data, form]);

  const saveM = useMutation({
    mutationFn: (payload: ModelConfigUpdateIn) => putModelConfig(botName, payload),
    onSuccess: () => {
      message.success(zhCN.modelConfig.saveSuccess);
      qc.invalidateQueries({ queryKey: ['model-config', botName] });
      qc.invalidateQueries({ queryKey: ['health', botName] });
    },
    onError: (e: unknown) => message.error(extractErrorMessage(e)),
  });

  const chatgptM = useMutation({
    mutationFn: () => putModelConfig(botName, CHATGPT_AUTH_DEFAULT),
    onSuccess: () => {
      message.success(zhCN.modelConfig.chatgptAuthApplied);
      qc.invalidateQueries({ queryKey: ['model-config', botName] });
      qc.invalidateQueries({ queryKey: ['health', botName] });
    },
    onError: (e: unknown) => message.error(extractErrorMessage(e)),
  });

  const onSubmit = (values: FormValues) => {
    const payload: ModelConfigUpdateIn = {
      provider: values.provider.trim(),
      model: values.model.trim(),
      base_url: values.base_url?.trim() || null,
      api_mode: values.api_mode?.trim() || null,
    };
    saveM.mutate(payload);
  };

  const isChatgptAuth = data?.is_chatgpt_auth ?? false;
  const notConfigured = !data?.provider || !data?.model;

  return (
    <div style={{ padding: 24 }} data-testid="model-config-page">
      <HealthSummary botName={botName} />
      <Card
        title={zhCN.modelConfig.tabTitle}
        size="small"
        extra={
          isChatgptAuth ? (
            <Tag color="green" data-testid="chatgpt-auth-tag">
              {zhCN.modelConfig.currentlyChatgptAuth}
            </Tag>
          ) : null
        }
      >
        {notConfigured && !isLoading && (
          <Alert
            type="warning"
            message={zhCN.modelConfig.notConfigured}
            style={{ marginBottom: 12 }}
            data-testid="model-not-configured"
          />
        )}
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Space
            direction="vertical"
            style={{
              width: '100%',
              padding: 12,
              border: '1px solid #f0f0f0',
              borderRadius: 6,
              background: '#fafafa',
            }}
          >
            <Typography.Text>{zhCN.modelConfig.chatgptAuthHint}</Typography.Text>
            <Button
              type="primary"
              loading={chatgptM.isPending}
              onClick={() => chatgptM.mutate()}
              data-testid="btn-chatgpt-auth"
            >
              {zhCN.modelConfig.chatgptAuthBtn}
            </Button>
          </Space>
          <Form<FormValues>
            form={form}
            layout="vertical"
            onFinish={onSubmit}
            data-testid="model-config-form"
          >
            <Form.Item
              label={zhCN.modelConfig.providerLabel}
              name="provider"
              rules={[{ required: true, message: zhCN.modelConfig.providerLabel }]}
            >
              <Input placeholder="openai-codex" data-testid="input-provider" />
            </Form.Item>
            <Form.Item
              label={zhCN.modelConfig.modelLabel}
              name="model"
              rules={[{ required: true, message: zhCN.modelConfig.modelLabel }]}
            >
              <Input placeholder="gpt-5.5" data-testid="input-model" />
            </Form.Item>
            <Form.Item label={zhCN.modelConfig.baseUrlLabel} name="base_url">
              <Input
                placeholder="https://chatgpt.com/backend-api/codex"
                data-testid="input-base-url"
              />
            </Form.Item>
            <Form.Item label={zhCN.modelConfig.apiModeLabel} name="api_mode">
              <Input placeholder="codex_responses" data-testid="input-api-mode" />
            </Form.Item>
            <Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                loading={saveM.isPending}
                data-testid="btn-save-model-config"
              >
                {zhCN.modelConfig.saveBtn}
              </Button>
            </Form.Item>
          </Form>
        </Space>
      </Card>
    </div>
  );
}
