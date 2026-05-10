import { useEffect, useMemo, useState } from 'react'
import { Button, DatePicker, Form, Input, message, Modal, Popconfirm, Select, Table, Tag } from 'antd'
import dayjs from 'dayjs'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'

interface Badge { consultant_id: string; consultant_name: string; is_me: boolean }
interface CTag { id: string; name: string; color: string }
type CourseStatusKey =
  | 'purchased_not_started'
  | 'sales_marked_completed'
  | 'purchased_not_started_refunded'
  | 'sales_marked_completed_refunded'
  | 'admin_marked_completed'
  | 'admin_marked_completed_refunded'

const COURSE_STATUS_META: Record<CourseStatusKey, { label: string; bg: string; color: string; border: string }> = {
  purchased_not_started: { label: '已购未上', bg: '#FDECEC', color: '#B42318', border: '#F7CACA' },
  sales_marked_completed: { label: '销售标记已上', bg: '#EAF8EE', color: '#166534', border: '#BFE7CB' },
  admin_marked_completed: { label: '管理员销课已上（扣结余）', bg: '#EAF2FF', color: '#1D4ED8', border: '#BFDBFE' },
  purchased_not_started_refunded: { label: '已购未上+退款', bg: '#FDECEC', color: '#B42318', border: '#F7CACA' },
  sales_marked_completed_refunded: { label: '销售标记已上+退款', bg: '#EAF8EE', color: '#166534', border: '#BFE7CB' },
  admin_marked_completed_refunded: { label: '管理员销课+退款', bg: '#EAF2FF', color: '#1D4ED8', border: '#BFDBFE' },
}

interface CProduct { product_id: string; product_name: string; is_refunded: boolean; status?: string | null }
interface TagOption { id: string; name: string; color: string; category_name: string }
interface RowItem {
  relation_id: string
  customer_id: string
  customer_name: string
  customer_info: string
  tags: CTag[]
  products: CProduct[]
  note: string | null
  sales_note: string | null
  tuition_balance: number
  next_consultation: string | null
  next_consultation_status: string
  next_consultation_label: string
  period_label: string
  period_status: string
  consultation_count: number
  is_refunded_customer: boolean
  row_tone: string
  collaborators: Badge[]
}
const toneBg: Record<string, string> = {
  danger: '#fff3f3',
  info: '#eef3f9',
  warn: '#fffbe6',
  muted: '#f7f7f7',
  normal: '#fff',
}
const y2f = (yuan: number) => `¥${Number(yuan || 0).toLocaleString()}`

export default function ConsultantCustomersPage() {
  const navigate = useNavigate()
  const [rows, setRows] = useState<RowItem[]>([])
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [tagTarget, setTagTarget] = useState<RowItem | null>(null)
  const [tagOptions, setTagOptions] = useState<TagOption[]>([])
  const [selectedTag, setSelectedTag] = useState<string | null>(null)
  const [editingNoteCustomerId, setEditingNoteCustomerId] = useState<string | null>(null)
  const [noteDraft, setNoteDraft] = useState('')
  const [savingNoteCustomerId, setSavingNoteCustomerId] = useState<string | null>(null)
  const [savedNoteCustomerId, setSavedNoteCustomerId] = useState<string | null>(null)
  const [editingPeriod, setEditingPeriod] = useState<RowItem | null>(null)
  const [periodForm] = Form.useForm()
  const [editingNextCustomerId, setEditingNextCustomerId] = useState<string | null>(null)

  const fetchRows = async (k = keyword) => {
    setLoading(true)
    try {
      const query = k ? `?keyword=${encodeURIComponent(k)}` : ''
      setRows(await api.get<RowItem[]>(`/consultant/customers${query}`))
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载我的咨询客户失败')
      setRows([])
    } finally {
      setLoading(false)
    }
  }

  const fetchTags = async () => {
    setTagOptions(await api.get<TagOption[]>('/consultant/tags'))
  }

  useEffect(() => { fetchRows('') }, [])

  const filtered = useMemo(() => rows, [rows])
  const stats = useMemo(() => {
    const totalConsultations = rows.reduce((sum, item) => sum + item.consultation_count, 0)
    const overdueNext = rows.filter((item) => item.next_consultation_status === 'overdue').length
    const todayNext = rows.filter((item) => item.next_consultation_status === 'today').length
    const refundedCustomers = rows.filter((item) => item.is_refunded_customer).length
    return { totalConsultations, overdueNext, todayNext, refundedCustomers }
  }, [rows])

  const saveNote = async (customerId: string, value: string) => {
    setSavingNoteCustomerId(customerId)
    try {
      const nextValue = value.trim() ? value : null
      await api.put(`/consultant/customers/${customerId}`, { note: nextValue })
      setRows((prev) => prev.map((item) => (item.customer_id === customerId ? { ...item, note: nextValue } : item)))
      setSavedNoteCustomerId(customerId)
      setTimeout(() => {
        setSavedNoteCustomerId((curr) => (curr === customerId ? null : curr))
      }, 1200)
    } finally {
      setSavingNoteCustomerId(null)
    }
  }

  const returnToPool = async (customerId: string) => {
    await api.post(`/consultant/customers/${customerId}/return-to-pool`)
    message.success('已退回咨询池')
    fetchRows()
  }

  const addTag = async () => {
    if (!tagTarget || !selectedTag) return
    await api.post(`/consultant/customers/${tagTarget.customer_id}/tags`, { tag_id: selectedTag })
    message.success('标签已添加')
    setTagTarget(null)
    setSelectedTag(null)
    fetchRows()
  }

  const removeTag = async (customerId: string, tagId: string) => {
    await api.delete(`/consultant/customers/${customerId}/tags/${tagId}`)
    fetchRows()
  }

  const columns = [
    {
      title: '客户',
      key: 'customer',
      width: 240,
      render: (_: unknown, r: RowItem) => (
        <div>
          <div style={{ fontWeight: 700, fontSize: 15, lineHeight: 1.2 }}>{r.customer_name}</div>
          <div style={{ color: '#8c8c8c', fontSize: 12, marginTop: 2 }}>{r.customer_info || '-'}</div>
          {r.collaborators.filter((c) => !c.is_me).length > 0 && (
            <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {r.collaborators
                .filter((c) => !c.is_me)
                .map((c) => (
                  <span
                    key={c.consultant_id}
                    style={{
                      fontSize: 11,
                      color: '#595959',
                      border: '1px solid #d9d9d9',
                      borderRadius: 10,
                      padding: '1px 8px',
                      background: '#fafafa',
                    }}
                  >
                    协作: {c.consultant_name}
                  </span>
                ))}
            </div>
          )}
        </div>
      ),
    },
    {
      title: '标签',
      key: 'tags',
      width: 240,
      render: (_: unknown, r: RowItem) => (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
          {r.tags.map((t) => (
            <Tag key={t.id} color={t.color} style={{ marginInlineEnd: 0, fontSize: 11, lineHeight: '16px' }}>
              {t.name}
              <button onClick={() => removeTag(r.customer_id, t.id)} style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'inherit', opacity: 0.6, padding: 0, marginLeft: 4, fontSize: 11 }}>×</button>
            </Tag>
          ))}
          <button
            onClick={() => { setTagTarget(r); setSelectedTag(null); fetchTags() }}
            style={{ border: '1px dashed #E8E8E3', background: 'none', cursor: 'pointer', color: '#8E8E8E', fontSize: 10, padding: '1px 6px', borderRadius: 3 }}
          >
            +
          </button>
        </div>
      ),
    },
    {
      title: '已购课程',
      key: 'products',
      width: 190,
      render: (_: unknown, r: RowItem) => (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-start' }}>
          {r.products.map((p) => (
            (() => {
              const key = (p.status || (p.is_refunded ? 'purchased_not_started_refunded' : 'purchased_not_started')) as CourseStatusKey
              const meta = COURSE_STATUS_META[key] || COURSE_STATUS_META.purchased_not_started
              const isRefunded = key.includes('refunded')
              return (
                <Tag
                  key={`${p.product_id}:${p.product_name}:${key}`}
                  style={{
                    marginInlineEnd: 0,
                    alignSelf: 'flex-start',
                    textDecoration: isRefunded ? 'line-through' : 'none',
                    color: meta.color,
                    borderColor: meta.border,
                    background: meta.bg,
                    paddingInline: 8,
                    lineHeight: '18px',
                    fontSize: 11,
                  }}
                >
                  {p.product_name}
                </Tag>
              )
            })()
          ))}
        </div>
      ),
    },
    {
      title: '咨询师备注',
      dataIndex: 'note',
      width: 240,
      render: (v: string | null, r: RowItem) => {
        const isEditing = editingNoteCustomerId === r.customer_id
        const isSaving = savingNoteCustomerId === r.customer_id
        const isSaved = savedNoteCustomerId === r.customer_id
        if (!isEditing) {
          return (
            <div
              onClick={() => {
                setEditingNoteCustomerId(r.customer_id)
                setNoteDraft(v || '')
              }}
              style={{
                minHeight: 30,
                padding: '4px 8px',
                border: '1px solid transparent',
                borderRadius: 8,
                cursor: 'text',
              }}
            >
              {v || <span style={{ color: '#bfbfbf' }}>点击填写备注</span>}
            </div>
          )
        }
        return (
          <div>
            <Input.TextArea
              value={noteDraft}
              autoFocus
              autoSize={{ minRows: 2, maxRows: 4 }}
              onChange={(e) => setNoteDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  e.preventDefault()
                  setEditingNoteCustomerId(null)
                  setNoteDraft('')
                  return
                }
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  saveNote(r.customer_id, noteDraft).then(() => {
                    setEditingNoteCustomerId(null)
                    setNoteDraft('')
                  })
                }
              }}
              onBlur={() => {
                saveNote(r.customer_id, noteDraft).then(() => {
                  setEditingNoteCustomerId(null)
                  setNoteDraft('')
                })
              }}
            />
            <div style={{ marginTop: 6, color: '#8c8c8c', fontSize: 12 }}>
              Enter 保存　Esc 取消　{isSaving ? '保存中...' : isSaved ? <span style={{ color: '#389e0d' }}>已自动保存</span> : ''}
            </div>
          </div>
        )
      },
    },
    {
      title: '销售备注',
      dataIndex: 'sales_note',
      width: 220,
      render: (v: string | null) => (
        <div style={{ color: '#595959', minHeight: 30, padding: '4px 8px' }}>
          {v || <span style={{ color: '#bfbfbf' }}>暂无销售备注</span>}
        </div>
      ),
    },
    {
      title: '剩余学费',
      dataIndex: 'tuition_balance',
      width: 120,
      align: 'right' as const,
      render: (v: number) => <span style={{ color: '#166534', fontWeight: 700 }}>{y2f(v)}</span>,
    },
    {
      title: '下次咨询',
      dataIndex: 'next_consultation_label',
      width: 130,
      render: (v: string, r: RowItem) => {
        const lines = v.split('\n')
        const color = r.next_consultation_status === 'overdue' ? '#a8071a' : '#003a8c'
        const isEditingNext = editingNextCustomerId === r.customer_id
        if (isEditingNext) {
          return (
            <DatePicker
              open
              autoFocus
              value={r.next_consultation ? dayjs(r.next_consultation) : null}
              showTime={{
                format: 'HH:00',
                disabledTime: () => ({
                  disabledMinutes: () => Array.from({ length: 59 }, (_, i) => i + 1),
                }),
              }}
              format="YYYY-MM-DD HH:00"
              style={{ width: 180 }}
              onChange={async (val) => {
                await api.put(`/consultant/customers/${r.customer_id}`, {
                  next_consultation: val ? dayjs(val).startOf('hour').toISOString() : null,
                })
                message.success('下次咨询已更新')
                setEditingNextCustomerId(null)
                fetchRows()
              }}
              onOpenChange={(open) => {
                if (!open) setEditingNextCustomerId(null)
              }}
            />
          )
        }
        return (
          <button
            onClick={() => {
              setEditingNextCustomerId(r.customer_id)
            }}
            style={{ border: 'none', background: 'transparent', padding: 0, textAlign: 'left', cursor: 'pointer' }}
          >
            <div><div style={{ color, fontWeight: 700 }}>{lines[0]}</div><div style={{ color, fontSize: 12 }}>{lines[1] || ''}</div></div>
          </button>
        )
      },
    },
    {
      title: '咨询周期',
      dataIndex: 'period_label',
      width: 130,
      align: 'center' as const,
      render: (v: string, r: RowItem) => {
        const lines = v.split('\n')
        const color = r.period_status === 'near_expiry' ? '#d46b08' : r.period_status === 'refunded' ? '#a8071a' : '#08979c'
        return (
          <button
            onClick={() => {
              setEditingPeriod(r)
              const [startText, endText] = lines[0].split('-')
              periodForm.setFieldsValue({
                start_date: startText && startText !== '--/--' ? dayjs(`${dayjs().year()}-${startText.replace('/', '-')}`) : null,
                end_date: endText && endText !== '--/--' ? dayjs(`${dayjs().year()}-${endText.replace('/', '-')}`) : null,
              })
            }}
            style={{ border: 'none', background: 'transparent', padding: 0, textAlign: 'center', cursor: 'pointer', display: 'inline-flex', justifyContent: 'center' }}
          >
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', lineHeight: 1.25 }}>
              <div style={{ fontWeight: 700 }}>{lines[0]}</div>
              <div style={{ color, fontSize: 12, marginTop: 2 }}>{lines[1] || ''}</div>
            </div>
          </button>
        )
      },
    },
    {
      title: '咨询次数',
      dataIndex: 'consultation_count',
      width: 100,
      render: (v: number, r: RowItem) => (
        <button
          onClick={() => navigate(`/consultant/customers/${r.customer_id}/logs`)}
          style={{ border: 'none', background: '#eef4ff', cursor: 'pointer', color: '#1d4ed8', fontWeight: 700, padding: '2px 8px', borderRadius: 999, minWidth: 42 }}
        >
          {v}
        </button>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 96,
      render: (_: unknown, r: RowItem) => (
        <Popconfirm title="确认退回咨询池？" onConfirm={() => returnToPool(r.customer_id)}>
          <Button size="small" type="default" style={{ borderColor: '#faad14', color: '#ad6800' }}>退回</Button>
        </Popconfirm>
      ),
    },
  ]

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>我的咨询客户</h2>
          <p className="page-subtitle">按下次咨询时间排序｜当前共 {filtered.length} 个咨询客户</p>
        </div>
        <Input.Search placeholder="搜索姓名/标签" style={{ width: 280 }} value={keyword} onChange={(e) => setKeyword(e.target.value)} onSearch={(v) => fetchRows(v)} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(160px, 1fr))', gap: 10, marginBottom: 12 }}>
        <div style={{ background: '#fff', border: '1px solid #e8e8e3', borderRadius: 10, padding: 12 }}>
          <div style={{ color: '#8c8c8c', fontSize: 12 }}>咨询客户</div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>{rows.length}</div>
        </div>
        <div style={{ background: '#fff', border: '1px solid #e8e8e3', borderRadius: 10, padding: 12 }}>
          <div style={{ color: '#8c8c8c', fontSize: 12 }}>总咨询次数</div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>{stats.totalConsultations}</div>
        </div>
        <div style={{ background: '#fff', border: '1px solid #e8e8e3', borderRadius: 10, padding: 12 }}>
          <div style={{ color: '#8c8c8c', fontSize: 12 }}>今日 / 逾期</div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>{stats.todayNext} / {stats.overdueNext}</div>
        </div>
        <div style={{ background: '#fff', border: '1px solid #e8e8e3', borderRadius: 10, padding: 12 }}>
          <div style={{ color: '#8c8c8c', fontSize: 12 }}>退款客户</div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>{stats.refundedCustomers}</div>
        </div>
      </div>

      <div style={{ background: '#fff', border: '1px solid #e8e8e3', borderRadius: 10, overflow: 'hidden' }}>
        <Table
          rowKey="relation_id"
          dataSource={filtered}
          columns={columns}
          loading={loading}
          pagination={false}
          size="small"
          onRow={(record) => ({ style: { background: toneBg[record.row_tone] || '#fff' } })}
        />
      </div>

      <Modal title="添加咨询师标签" open={!!tagTarget} onOk={addTag} onCancel={() => { setTagTarget(null); setSelectedTag(null) }}>
        <Select value={selectedTag} onChange={setSelectedTag} style={{ width: '100%' }} placeholder="搜索并选择标签" showSearch optionFilterProp="label">
          {tagOptions.map((t) => (
            <Select.Option key={t.id} value={t.id} label={t.name}>
              <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: t.color, marginRight: 6 }} />
              {t.name}
              <span style={{ color: '#B8B8B8', fontSize: 10, marginLeft: 6 }}>{t.category_name}</span>
            </Select.Option>
          ))}
        </Select>
      </Modal>

      <Modal
        title={editingPeriod ? `编辑咨询周期 · ${editingPeriod.customer_name}` : '编辑咨询周期'}
        open={!!editingPeriod}
        onOk={async () => {
          if (!editingPeriod) return
          const v = await periodForm.validateFields()
          await api.put(`/consultant/customers/${editingPeriod.customer_id}`, {
            start_date: v.start_date ? dayjs(v.start_date).format('YYYY-MM-DD') : null,
            end_date: v.end_date ? dayjs(v.end_date).format('YYYY-MM-DD') : null,
          })
          message.success('咨询周期已更新')
          setEditingPeriod(null)
          fetchRows()
        }}
        onCancel={() => setEditingPeriod(null)}
      >
        <Form form={periodForm} layout="vertical">
          <div style={{ display: 'flex', gap: 12 }}>
            <Form.Item name="start_date" label="开始日期" style={{ flex: 1 }}>
              <DatePicker style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="end_date" label="结束日期" style={{ flex: 1 }}>
              <DatePicker style={{ width: '100%' }} />
            </Form.Item>
          </div>
        </Form>
      </Modal>

    </div>
  )
}
