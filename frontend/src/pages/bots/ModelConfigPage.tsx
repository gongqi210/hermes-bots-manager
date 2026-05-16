// Phase 5: Model configuration page (chat tab).
// The ChatGPT auth action launches the Codex browser authorization flow. The
// form below reflects Hermes' own provider/model catalog instead of exposing
// transport-level fields as hand-written config.

import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Form,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getModelConfig,
  putModelConfig,
  startChatgptAuth,
} from '@/api/management';
import type { ModelConfigUpdateIn } from '@/api/types';
import { zhCN } from '@/i18n/zh-CN';
import { extractErrorMessage } from '@/utils/errors';
import HealthSummary from './HealthSummary';
import {
  providerOptionMatchesSearch,
  providerSearchText,
} from './modelConfigSearch';

interface FormValues {
  provider: string;
  model: string;
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
      });
    }
  }, [data, form]);

  const selectedProviderSlug = Form.useWatch('provider', form);
  const providers = data?.providers ?? [];
  const selectedProvider =
    providers.find((provider) => provider.slug === selectedProviderSlug) ??
    providers.find((provider) => provider.slug === data?.provider);
  const toProviderOption = (provider: (typeof providers)[number]) => ({
    value: provider.slug,
    label: `${provider.name} (${provider.slug})`,
    searchText: providerSearchText(provider),
  });
  const configuredProviderOptions = providers
    .filter((provider) => provider.is_configured)
    .map(toProviderOption);
  const builtinProviderOptions = providers
    .filter((provider) => !provider.is_configured)
    .map(toProviderOption);
  const providerOptions = [
    configuredProviderOptions.length
      ? {
          label: zhCN.modelConfig.configuredProvidersGroup,
          options: configuredProviderOptions,
        }
      : null,
    builtinProviderOptions.length
      ? {
          label: zhCN.modelConfig.builtinProvidersGroup,
          options: builtinProviderOptions,
        }
      : null,
  ].filter((group): group is NonNullable<typeof group> => group !== null);
  const modelOptions = (selectedProvider?.models ?? []).map((model) => ({
    value: model,
    label: model,
  }));
  const showCodexAuth = selectedProviderSlug === 'openai-codex' || data?.provider === 'openai-codex';

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
    mutationFn: () => startChatgptAuth(botName),
    onSuccess: (result) => {
      window.open(result.authorization_url, '_blank', 'noopener,noreferrer');
      if (result.user_code) {
        message.info(`授权验证码：${result.user_code}`);
      }
      message.success(result.message || zhCN.modelConfig.chatgptAuthStarted);
    },
    onError: (e: unknown) => message.error(extractErrorMessage(e)),
  });

  const onSubmit = (values: FormValues) => {
    const payload: ModelConfigUpdateIn = {
      provider: values.provider.trim(),
      model: values.model.trim(),
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
          <Form<FormValues>
            form={form}
            layout="vertical"
            onFinish={onSubmit}
            data-testid="model-config-form"
          >
            {showCodexAuth && (
              <Space
                direction="vertical"
                style={{
                  width: '100%',
                  marginBottom: 12,
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
            )}
            {!isLoading && providers.length === 0 && (
              <Alert
                type="info"
                message={zhCN.modelConfig.noProviderOptions}
                style={{ marginBottom: 12 }}
                data-testid="no-provider-options"
              />
            )}
            <Form.Item
              label={zhCN.modelConfig.providerLabel}
              name="provider"
              rules={[{ required: true, message: zhCN.modelConfig.providerLabel }]}
            >
              <Select
                showSearch
                options={providerOptions}
                placeholder={zhCN.modelConfig.providerPlaceholder}
                optionFilterProp="label"
                filterOption={providerOptionMatchesSearch}
                data-testid="select-provider"
                onChange={(slug) => {
                  const nextProvider = providers.find((provider) => provider.slug === slug);
                  const currentModel = form.getFieldValue('model');
                  if (
                    nextProvider?.models.length &&
                    !nextProvider.models.includes(currentModel)
                  ) {
                    form.setFieldValue('model', nextProvider.models[0]);
                  }
                }}
              />
            </Form.Item>
            <Form.Item
              label={zhCN.modelConfig.modelLabel}
              name="model"
              rules={[{ required: true, message: zhCN.modelConfig.modelLabel }]}
            >
              <AutoComplete
                options={modelOptions}
                placeholder={zhCN.modelConfig.modelPlaceholder}
                filterOption={(input, option) =>
                  (option?.value ?? '').toLowerCase().includes(input.toLowerCase())
                }
                data-testid="input-model"
              />
            </Form.Item>
            {selectedProvider && (
              <Space
                direction="vertical"
                size={4}
                style={{
                  width: '100%',
                  marginBottom: 16,
                  padding: 12,
                  border: '1px solid #f0f0f0',
                  borderRadius: 6,
                  background: '#fafafa',
                }}
                data-testid="provider-transport-summary"
              >
                <Typography.Text type="secondary">
                  {zhCN.modelConfig.transportManaged}
                </Typography.Text>
                <Typography.Text>
                  {zhCN.modelConfig.baseUrlLabel}: {selectedProvider.base_url ?? 'Hermes 默认'}
                </Typography.Text>
                <Typography.Text>
                  {zhCN.modelConfig.apiModeLabel}: {selectedProvider.api_mode ?? 'Hermes 默认'}
                </Typography.Text>
                <Typography.Text type="secondary">
                  {zhCN.modelConfig.providerSourceLabel}: {selectedProvider.source || 'Hermes'}
                </Typography.Text>
                {!selectedProvider.is_configured && (
                  <Typography.Text type="warning">
                    {zhCN.modelConfig.providerNeedsCredentials}
                  </Typography.Text>
                )}
              </Space>
            )}
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
