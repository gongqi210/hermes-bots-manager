// Phase 5: Workspace page — edits terminal.cwd in config.yaml.
// Supports three modes: A (manual input), B (library picker), C (reuse other bot).

import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Radio,
  Select,
  Space,
  Tag,
  Tooltip,
  message,
} from 'antd';
import { DeleteOutlined as DeleteIcon } from '@ant-design/icons';
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { gatewayAction } from '@/api/gateway';
import {
  addWorkspaceLibraryEntry,
  deleteWorkspaceLibraryEntry,
  getHealth,
  getWorkspace,
  getWorkspaceLibrary,
  getWorkspaceReuseOptions,
  putWorkspace,
} from '@/api/management';
import type { WorkspaceLibraryItem, WorkspaceReuseOption } from '@/api/management';
import type { WorkspaceOut, WorkspaceStatus } from '@/api/types';
import { zhCN } from '@/i18n/zh-CN';
import { extractErrorMessage } from '@/utils/errors';
import { useRole } from '@/hooks/useRole';
import HealthSummary from './HealthSummary';

const STATUS_COLOR: Record<WorkspaceStatus, string> = {
  ok: 'green',
  warning: 'gold',
  error: 'red',
  unset: 'default',
};

const STATUS_TEXT: Record<WorkspaceStatus, string> = {
  ok: zhCN.workspace.statusOk,
  warning: zhCN.workspace.statusWarning,
  error: zhCN.workspace.statusError,
  unset: zhCN.workspace.statusUnset,
};

type WorkspaceMode = 'manual' | 'library' | 'reuse';

interface FormValues {
  cwd: string;
}

interface SaveResult {
  workspace: WorkspaceOut;
  restarted: boolean;
  restartError?: string;
}

function YesNo({ value }: { value: boolean }) {
  return <Tag color={value ? 'green' : 'default'}>{value ? '✓' : '—'}</Tag>;
}

export default function WorkspacePage({ botName }: { botName: string }) {
  const qc = useQueryClient();
  const [form] = Form.useForm<FormValues>();
  const [addForm] = Form.useForm<{ path: string; label?: string }>();
  const role = useRole();
  const isViewer = role === 'Viewer';

  const [mode, setMode] = useState<WorkspaceMode>('manual');
  const [libraryModalOpen, setLibraryModalOpen] = useState(false);
  const [addModalOpen, setAddModalOpen] = useState(false);

  const { data } = useQuery<WorkspaceOut>({
    queryKey: ['workspace', botName],
    queryFn: () => getWorkspace(botName),
  });

  const { data: libraryItems, refetch: refetchLibrary } = useQuery<WorkspaceLibraryItem[]>({
    queryKey: ['workspace-library'],
    queryFn: getWorkspaceLibrary,
    enabled: libraryModalOpen,
  });

  const { data: reuseOptions } = useQuery<WorkspaceReuseOption[]>({
    queryKey: ['workspace-reuse', botName],
    queryFn: () => getWorkspaceReuseOptions(botName),
    enabled: mode === 'reuse',
  });

  useEffect(() => {
    if (data) form.setFieldsValue({ cwd: data.cwd ?? '' });
  }, [data, form]);

  const handleModeChange = (newMode: WorkspaceMode) => {
    setMode(newMode);
  };

  const addLibraryM = useMutation({
    mutationFn: (values: { path: string; label?: string }) =>
      addWorkspaceLibraryEntry(values),
    onSuccess: () => {
      message.success('已添加到库');
      addForm.resetFields();
      setAddModalOpen(false);
      refetchLibrary();
    },
    onError: (e: unknown) => message.error(extractErrorMessage(e)),
  });

  const deleteLibraryM = useMutation({
    mutationFn: (id: number) => deleteWorkspaceLibraryEntry(id),
    onSuccess: () => {
      message.success('已从库中删除');
      refetchLibrary();
    },
    onError: (e: unknown) => message.error(extractErrorMessage(e)),
  });

  const saveM = useMutation<SaveResult, unknown, string | null>({
    mutationFn: async (cwd: string | null) => {
      const health = await getHealth(botName).catch(() => null);
      const workspace = await putWorkspace(botName, { cwd });

      if (health?.gateway_state !== 'running') {
        return { workspace, restarted: false };
      }

      try {
        await gatewayAction(botName, 'restart');
        return { workspace, restarted: true };
      } catch (error) {
        return {
          workspace,
          restarted: false,
          restartError: extractErrorMessage(error),
        };
      }
    },
    onSuccess: (result) => {
      if (result.restartError) {
        message.warning(zhCN.workspace.saveRestartFailed(result.restartError));
      } else if (result.restarted) {
        message.success(zhCN.workspace.saveSuccessRestarted);
      } else {
        message.success(zhCN.workspace.saveSuccess);
      }
      qc.invalidateQueries({ queryKey: ['workspace', botName] });
      qc.invalidateQueries({ queryKey: ['health', botName] });
      qc.invalidateQueries({ queryKey: ['gateway-status', botName] });
    },
    onError: (e: unknown) => message.error(extractErrorMessage(e)),
  });

  const onSubmit = (values: FormValues) => {
    const trimmed = values.cwd.trim();
    saveM.mutate(trimmed === '' ? null : trimmed);
  };

  const isAdminOrOwner = role === 'Admin' || role === 'Owner';

  return (
    <div style={{ padding: 24 }} data-testid="workspace-page">
      <HealthSummary botName={botName} />
      <Card title={zhCN.workspace.tabTitle} size="small">
        {/* Mode selector */}
        <Tooltip title={isViewer ? '仅 Editor 以上可修改' : undefined}>
          <Radio.Group
            value={mode}
            onChange={(e) => handleModeChange(e.target.value as WorkspaceMode)}
            disabled={isViewer}
            style={{ marginBottom: 16 }}
            data-testid="workspace-mode-group"
          >
            <Radio.Button value="manual">手动输入</Radio.Button>
            <Radio.Button value="library" data-testid="mode-library-btn">从库中选择</Radio.Button>
            <Radio.Button value="reuse" data-testid="mode-reuse-btn">复用其他 Bot</Radio.Button>
          </Radio.Group>
        </Tooltip>

        <Form<FormValues>
          form={form}
          layout="vertical"
          onFinish={onSubmit}
          data-testid="workspace-form"
        >
          {/* Mode B: library picker trigger */}
          {mode === 'library' && (
            <Form.Item label="从库中选择工作目录">
              <Space direction="vertical" style={{ width: '100%' }}>
                <Button
                  onClick={() => setLibraryModalOpen(true)}
                  data-testid="btn-open-library"
                  disabled={isViewer}
                >
                  从库中选择
                </Button>
              </Space>
            </Form.Item>
          )}

          {/* Mode C: reuse another bot's cwd */}
          {mode === 'reuse' && (
            <Form.Item label="复用其他 Bot 的工作目录">
              <Select
                placeholder="选择一个 Bot"
                style={{ width: '100%' }}
                disabled={isViewer}
                data-testid="select-reuse-bot"
                onChange={(val: string) => {
                  form.setFieldsValue({ cwd: val });
                }}
                options={(reuseOptions ?? []).map((opt) => ({
                  label: `${opt.bot_name}: ${opt.cwd}`,
                  value: opt.cwd,
                }))}
              />
            </Form.Item>
          )}

          <Form.Item
            label={zhCN.workspace.cwdLabel}
            name="cwd"
            extra={zhCN.workspace.cwdHint}
            rules={[
              {
                validator: (_rule, value: string) => {
                  if (!value || value.trim() === '') return Promise.resolve();
                  if (!value.trim().startsWith('/')) {
                    return Promise.reject(new Error('必须是绝对路径 (以 / 开头)'));
                  }
                  return Promise.resolve();
                },
              },
            ]}
          >
            <Input
              placeholder={zhCN.workspace.cwdPlaceholder}
              data-testid="input-workspace-cwd"
              readOnly={mode !== 'manual'}
            />
          </Form.Item>
          <Form.Item>
            <Tooltip title={isViewer ? '仅 Editor 以上可修改' : undefined}>
              <Space>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={saveM.isPending}
                  disabled={isViewer}
                  data-testid="btn-save-workspace"
                >
                  {zhCN.workspace.saveBtn}
                </Button>
                <Button
                  onClick={() => {
                    form.setFieldsValue({ cwd: '' });
                    saveM.mutate(null);
                  }}
                  disabled={isViewer}
                  data-testid="btn-clear-workspace"
                >
                  {zhCN.workspace.clearBtn}
                </Button>
              </Space>
            </Tooltip>
          </Form.Item>
        </Form>
        {data && (
          <>
            <Alert
              type={
                data.status === 'ok'
                  ? 'success'
                  : data.status === 'warning'
                    ? 'warning'
                    : data.status === 'error'
                      ? 'error'
                      : 'info'
              }
              message={
                <span data-testid="workspace-status-message">
                  <Tag color={STATUS_COLOR[data.status]}>{STATUS_TEXT[data.status]}</Tag>
                  {data.message}
                </span>
              }
              style={{ marginBottom: 12 }}
            />
            <Descriptions size="small" column={2}>
              <Descriptions.Item label={zhCN.workspace.fieldExists}>
                <YesNo value={data.exists} />
              </Descriptions.Item>
              <Descriptions.Item label={zhCN.workspace.fieldIsDir}>
                <YesNo value={data.is_directory} />
              </Descriptions.Item>
              <Descriptions.Item label={zhCN.workspace.fieldReadable}>
                <YesNo value={data.readable} />
              </Descriptions.Item>
              <Descriptions.Item label={zhCN.workspace.fieldWritable}>
                <YesNo value={data.writable} />
              </Descriptions.Item>
            </Descriptions>
          </>
        )}
      </Card>

      {/* Mode B: Library Modal */}
      <Modal
        title="工作目录库"
        open={libraryModalOpen}
        onCancel={() => setLibraryModalOpen(false)}
        footer={
          isAdminOrOwner ? (
            <Button
              onClick={() => setAddModalOpen(true)}
              data-testid="btn-add-to-library"
            >
              添加到库
            </Button>
          ) : null
        }
        data-testid="library-modal"
      >
        {(libraryItems?.length ?? 0) === 0 ? (
          <Empty description="暂无已登记 Workspace，点击添加" />
        ) : (
          <List
            dataSource={libraryItems}
            renderItem={(item) => (
              <List.Item
                key={item.id}
                actions={
                  isAdminOrOwner
                    ? [
                        <Button
                          key="delete"
                          type="text"
                          danger
                          icon={<DeleteIcon />}
                          onClick={() => deleteLibraryM.mutate(item.id)}
                          data-testid={`btn-delete-library-${item.id}`}
                        />,
                      ]
                    : undefined
                }
                onClick={() => {
                  form.setFieldsValue({ cwd: item.path });
                  setLibraryModalOpen(false);
                }}
                style={{ cursor: 'pointer' }}
                data-testid={`library-item-${item.id}`}
              >
                <List.Item.Meta
                  title={item.label ?? item.path}
                  description={
                    <span style={{ fontFamily: 'monospace' }}>{item.path}</span>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Modal>

      {/* Add to Library Modal */}
      <Modal
        title="添加到工作目录库"
        open={addModalOpen}
        onCancel={() => setAddModalOpen(false)}
        onOk={() => addForm.submit()}
        confirmLoading={addLibraryM.isPending}
        data-testid="add-library-modal"
      >
        <Form
          form={addForm}
          layout="vertical"
          onFinish={(values) => addLibraryM.mutate(values)}
        >
          <Form.Item
            label="绝对路径"
            name="path"
            rules={[{ required: true, message: '路径不能为空' }]}
          >
            <Input placeholder="/absolute/path/to/workspace" data-testid="input-library-path" />
          </Form.Item>
          <Form.Item label="标签（可选）" name="label">
            <Input placeholder="便于识别的名称" data-testid="input-library-label" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
