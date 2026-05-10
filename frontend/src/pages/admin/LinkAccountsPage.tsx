import { useEffect, useState } from 'react'
import { Table, Modal, Form, Input, Select, Popconfirm, message } from 'antd'
import { PlusOutlined, SwapOutlined } from '@ant-design/icons'
import { api } from '../../api/client'

interface LinkAccount {
  id: string
  account_id: string
  owner_id: string
  owner_name: string | null
  customer_count: number
  created_at: string | null
  last_transfer_at: string | null
  last_transfer_from_owner_name: string | null
}

interface UserOption { id: string; name: string }

function timeAgo(d: string | null): string {
  if (!d) return '-'
  const days = Math.floor((Date.now() - new Date(d).getTime()) / 86400000)
  if (days <= 0) return '今天'
  return `${days} 天前`
}

function transferText(lastTransferAt: string | null, fromOwnerName: string | null): string {
  if (!lastTransferAt || !fromOwnerName) return '未流转过'
  const start = new Date(lastTransferAt)
  if (Number.isNaN(start.getTime())) return '未流转过'

  const now = new Date()
  const startDay = new Date(start.getFullYear(), start.getMonth(), start.getDate()).getTime()
  const nowDay = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const diffDays = Math.max(0, Math.floor((nowDay - startDay) / 86400000))
  const when = diffDays === 0 ? '今天' : `${diffDays} 天前`
  return `${when}从${fromOwnerName}销售流转`
}

export default function LinkAccountsPage() {
  const [accounts, setAccounts] = useState<LinkAccount[]>([])
  const [users, setUsers] = useState<UserOption[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [transferModal, setTransferModal] = useState<LinkAccount | null>(null)
  const [form] = Form.useForm()
  const [transferForm] = Form.useForm()

  const fetchAccounts = async () => {
    setLoading(true)
    try {
      setAccounts(await api.get<LinkAccount[]>('/link-accounts/'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAccounts()
    api.get<UserOption[]>('/users/?role=sales').then(setUsers)
  }, [])

  const handleCreate = async () => {
    try {
      await api.post('/link-accounts/', await form.validateFields())
      message.success('账号已创建')
      setModalOpen(false)
      form.resetFields()
      fetchAccounts()
    } catch {
      message.error('操作失败')
    }
  }

  const handleTransfer = async () => {
    if (!transferModal) return
    try {
      await api.post(`/link-accounts/${transferModal.id}/transfer`, await transferForm.validateFields())
      message.success('流转成功')
      setTransferModal(null)
      transferForm.resetFields()
      fetchAccounts()
    } catch {
      message.error('流转失败')
    }
  }

  const handleDelete = async (account: LinkAccount) => {
    if (account.customer_count > 0) {
      message.warning(`该工作账号下仍有 ${account.customer_count} 个客户，请先流转或处理客户后再删除`)
      return
    }
    try {
      await api.delete(`/link-accounts/${account.id}`)
      message.success('账号已删除')
      fetchAccounts()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const columns = [
    {
      title: '账号 ID',
      dataIndex: 'account_id',
      width: 200,
      render: (v: string) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: '#0E0E0E' }}>{v}</span>,
    },
    {
      title: '归属销售',
      width: 120,
      render: (_: unknown, r: LinkAccount) => r.owner_name
        ? <span style={{ fontSize: 12, padding: '1px 8px', borderRadius: 4, background: '#F2F2ED' }}>{r.owner_name}</span>
        : <span style={{ color: '#B8B8B8', fontSize: 12 }}>-</span>,
    },
    {
      title: '客户数',
      dataIndex: 'customer_count',
      width: 80,
      render: (v: number) => <span style={{ fontWeight: 600, fontFamily: 'var(--font-mono)', color: v > 0 ? '#0E0E0E' : '#B8B8B8' }}>{v || 0}</span>,
    },
    {
      title: '创建',
      dataIndex: 'created_at',
      width: 100,
      render: (v: string | null) => <span style={{ fontSize: 12, color: '#8E8E8E' }}>{timeAgo(v)}</span>,
    },
    {
      title: '流转记录',
      width: 220,
      render: (_: unknown, r: LinkAccount) => <span style={{ fontSize: 12, color: '#595959' }}>{transferText(r.last_transfer_at, r.last_transfer_from_owner_name)}</span>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 130,
      render: (_: unknown, r: LinkAccount) => (
        <div style={{ display: 'flex', gap: 14 }}>
          <button className="action-link" style={{ color: '#8E8E8E', display: 'flex', alignItems: 'center', gap: 3 }}
            onClick={() => { setTransferModal(r); transferForm.resetFields() }}
            onMouseEnter={(e) => (e.currentTarget as HTMLElement).style.color = '#0E0E0E'}
            onMouseLeave={(e) => (e.currentTarget as HTMLElement).style.color = '#8E8E8E'}
          ><SwapOutlined style={{ fontSize: 11 }} /> 流转</button>
          <Popconfirm
            title={r.customer_count > 0 ? `该账号下仍绑定 ${r.customer_count} 个客户` : '确认删除？'}
            description={r.customer_count > 0 ? '请先流转或处理客户后再删除该工作账号' : '删除后将无法恢复'}
            onConfirm={() => handleDelete(r)}
          >
            <button className="action-link" style={{ color: '#B8B8B8' }}
              onMouseEnter={(e) => (e.currentTarget as HTMLElement).style.color = '#DC2626'}
              onMouseLeave={(e) => (e.currentTarget as HTMLElement).style.color = '#B8B8B8'}
            >删除</button>
          </Popconfirm>
        </div>
      ),
    },
  ]

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>工作账号管理</h2>
          <p className="page-subtitle">管理微信等关联账号，支持一键流转到其他销售</p>
        </div>
        <button
          onClick={() => { form.resetFields(); setModalOpen(true) }}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 6, padding: '7px 16px', borderRadius: 8,
            border: 'none', background: '#0E0E0E', color: '#fff', fontSize: 13, fontWeight: 600,
            cursor: 'pointer', fontFamily: 'inherit', transition: 'background 0.15s',
          }}
          onMouseEnter={(e) => (e.currentTarget as HTMLElement).style.background = '#4A4A4A'}
          onMouseLeave={(e) => (e.currentTarget as HTMLElement).style.background = '#0E0E0E'}
        >
          <PlusOutlined /> 新增账号
        </button>
      </div>

      <div style={{ background: '#fff', borderRadius: 10, border: '1px solid #E8E8E3', overflow: 'hidden' }}>
        <Table rowKey="id" dataSource={accounts} columns={columns} loading={loading} pagination={false} />
      </div>

      <Modal title="新增账号" open={modalOpen} onOk={handleCreate} onCancel={() => setModalOpen(false)} width={420}>
        <Form form={form} layout="vertical">
          <Form.Item name="account_id" label="账号 ID" rules={[{ required: true }]}><Input placeholder="wxid_xxxx" /></Form.Item>
          <Form.Item name="owner_id" label="归属销售" rules={[{ required: true }]}>
            <Select showSearch optionFilterProp="label" placeholder="选择销售">
              {users.map((u) => <Select.Option key={u.id} value={u.id} label={u.name}>{u.name}</Select.Option>)}
            </Select>
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="流转账号" open={!!transferModal} onOk={handleTransfer} onCancel={() => setTransferModal(null)} width={440}>
        {transferModal && (
          <div>
            <div style={{ background: '#F2F2ED', borderRadius: 8, padding: '12px 14px', marginBottom: 20, fontSize: 13 }}>
              <div>当前归属：<strong>{transferModal.owner_name || '无'}</strong></div>
              <div>客户数：<strong>{transferModal.customer_count}</strong></div>
              <div style={{ fontSize: 11, color: '#8E8E8E', marginTop: 4 }}>流转后所有客户跟随迁移</div>
            </div>
            <Form form={transferForm} layout="vertical">
              <Form.Item name="target_user_id" label="目标销售" rules={[{ required: true }]}>
                <Select showSearch optionFilterProp="label" placeholder="选择目标销售">
                  {users.filter((u) => u.id !== transferModal.owner_id).map((u) => <Select.Option key={u.id} value={u.id} label={u.name}>{u.name}</Select.Option>)}
                </Select>
              </Form.Item>
              <Form.Item name="reason" label="流转原因" rules={[{ required: true }]}>
                <Input.TextArea rows={2} placeholder="请简要说明原因..." />
              </Form.Item>
            </Form>
          </div>
        )}
      </Modal>
    </div>
  )
}
