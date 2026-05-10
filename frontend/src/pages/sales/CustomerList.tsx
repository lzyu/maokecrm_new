import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, DatePicker, Form, Input, InputNumber, Modal, Select, Table, Tag, message } from 'antd'
import dayjs from 'dayjs'
import { api } from '../../api/client'
import { useNavigate } from 'react-router-dom'

interface LinkAccount { id: string; account_id: string; customer_count: number; is_active: boolean }
interface CustomerTag { id: string; name: string; color: string }
interface TagOption { id: string; name: string; color: string; category_name: string }
interface ProductOption { id: string; name: string; price: number; is_consultation: boolean; status: string }
interface CourseItem { enrollment_id: string; product_id: string; product_name: string; amount_paid: number; refunded_amount: number; status: string }
interface Customer {
  id: string
  name: string
  phone: string | null
  client_wechat_name: string | null
  industry: string | null
  region: string | null
  added_date: string
  other_contact: string | null
  link_account_id: string
  link_account_name: string | null
  tags: CustomerTag[]
  note: string | null
  next_follow_up: string | null
  next_follow_up_status: 'overdue' | 'today' | 'future' | 'unset'
  in_consultation_pool: boolean
  consultation_count: number | null
  courses: CourseItem[]
  total_spent: number
  gifted_tuition_amount: number
  tuition_balance: number
}

interface TuitionGiftRequestItem {
  id: string
  customer_id: string
  customer_name: string
  amount: number
  sales_note: string | null
  admin_note: string | null
  status: 'pending' | 'approved' | 'rejected'
  reviewed_at: string | null
  created_at: string
}

interface DuplicateMatch {
  customer_id: string
  customer_name: string
  phone: string | null
  client_wechat_name: string | null
  owner_id: string
  owner_name: string | null
  link_account_name: string | null
  consultant_names: string[]
  matched_fields: string[]
}

interface DuplicateCheckResult {
  exists: boolean
  matches: DuplicateMatch[]
}


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

const y2f = (yuan: number) => `¥${Number(yuan || 0).toLocaleString()}`

const followUpLabel = (item: Customer) => {
  if (!item.next_follow_up) return '未设置'
  const d = dayjs(item.next_follow_up)
  if (item.next_follow_up_status === 'today') return `今天\n${d.format('HH:00')}`
  return `${d.format('M/D')}\n${d.format('HH:00')}`
}

const salesStatusSet = new Set<CourseStatusKey>([
  'purchased_not_started',
  'sales_marked_completed',
  'purchased_not_started_refunded',
  'sales_marked_completed_refunded',
])

export default function CustomerList() {
  const navigate = useNavigate()
  const [linkAccounts, setLinkAccounts] = useState<LinkAccount[]>([])
  const [customers, setCustomers] = useState<Customer[]>([])
  const [loading, setLoading] = useState(false)

  const [selectedLA, setSelectedLA] = useState<string | null>(null)
  const [keyword, setKeyword] = useState('')

  const [addWechatOpen, setAddWechatOpen] = useState(false)
  const [addWechatForm] = Form.useForm()
  const [createCustomerOpen, setCreateCustomerOpen] = useState(false)
  const [createCustomerSubmitting, setCreateCustomerSubmitting] = useState(false)
  const [createCustomerForm] = Form.useForm()

  const [tagTarget, setTagTarget] = useState<Customer | null>(null)
  const [tagOptions, setTagOptions] = useState<TagOption[]>([])
  const [selectedTag, setSelectedTag] = useState<string | null>(null)

  const [courseTarget, setCourseTarget] = useState<Customer | null>(null)
  const [productOptions, setProductOptions] = useState<ProductOption[]>([])
  const [selectedProductId, setSelectedProductId] = useState<string | null>(null)
  const [newCourseAmount, setNewCourseAmount] = useState<number | null>(null)
  const [activeCourseKey, setActiveCourseKey] = useState<string | null>(null)
  const [courseActionLoadingKey, setCourseActionLoadingKey] = useState<string | null>(null)
  const [editingAmountCourse, setEditingAmountCourse] = useState<{ customerId: string; enrollmentId: string; amountPaid: number } | null>(null)
  const [editingRefundCourse, setEditingRefundCourse] = useState<{ customerId: string; enrollmentId: string; maxRefund: number } | null>(null)
  const [amountForm] = Form.useForm<{ amount_yuan: number }>()
  const [refundForm] = Form.useForm<{ refund_yuan: number }>()
  const activeCourseWrapRef = useRef<HTMLDivElement | null>(null)

  const [editingNoteCustomerId, setEditingNoteCustomerId] = useState<string | null>(null)
  const [noteDraft, setNoteDraft] = useState('')
  const [savingNoteCustomerId, setSavingNoteCustomerId] = useState<string | null>(null)
  const [savedNoteCustomerId, setSavedNoteCustomerId] = useState<string | null>(null)
  const [editingNextCustomerId, setEditingNextCustomerId] = useState<string | null>(null)
  const [giftRequestOpen, setGiftRequestOpen] = useState(false)
  const [giftRequestForm] = Form.useForm()
  const [giftHistoryOpen, setGiftHistoryOpen] = useState(false)
  const [giftHistoryLoading, setGiftHistoryLoading] = useState(false)
  const [giftHistory, setGiftHistory] = useState<TuitionGiftRequestItem[]>([])
  const [duplicateOpen, setDuplicateOpen] = useState(false)
  const [duplicateMatches, setDuplicateMatches] = useState<DuplicateMatch[]>([])

  const fetchLinkAccounts = useCallback(async () => {
    setLinkAccounts(await api.get<LinkAccount[]>('/sales/link-accounts'))
  }, [])

  const fetchCustomers = useCallback(async () => {
    setLoading(true)
    try {
      const q: string[] = []
      if (selectedLA) q.push(`link_account_id=${encodeURIComponent(selectedLA)}`)
      if (keyword.trim()) q.push(`keyword=${encodeURIComponent(keyword.trim())}`)
      const query = q.length ? `?${q.join('&')}` : ''
      setCustomers(await api.get<Customer[]>(`/sales/customers${query}`))
    } finally {
      setLoading(false)
    }
  }, [selectedLA, keyword])

  const fetchTags = async () => setTagOptions(await api.get<TagOption[]>('/sales/tags'))
  const fetchProducts = async () => setProductOptions(await api.get<ProductOption[]>('/sales/products'))

  useEffect(() => { fetchLinkAccounts() }, [fetchLinkAccounts])
  useEffect(() => { fetchCustomers() }, [fetchCustomers])

  const totalCustomers = useMemo(() => linkAccounts.reduce((s, a) => s + a.customer_count, 0), [linkAccounts])

  const submitAddWechat = async () => {
    const v = await addWechatForm.validateFields()
    await api.post('/sales/link-accounts', { account_id: v.account_id })
    message.success('微信号已新增')
    setAddWechatOpen(false)
    addWechatForm.resetFields()
    fetchLinkAccounts()
  }

  const submitCreateCustomer = async () => {
    const v = await createCustomerForm.validateFields()
    setCreateCustomerSubmitting(true)
    try {
      const payload = {
        name: v.name,
        phone: v.phone || undefined,
        client_wechat_name: v.client_wechat_name,
        industry: v.industry || undefined,
        region: v.region || undefined,
        link_account_id: v.link_account_id,
        added_date: (v.added_date || dayjs()).format('YYYY-MM-DD'),
        other_contact: v.other_contact || undefined,
      }
      const check = await api.post<DuplicateCheckResult>('/sales/customers/check-duplicate', {
        phone: payload.phone,
        client_wechat_name: payload.client_wechat_name,
      })
      if (check.exists) {
        setDuplicateMatches(check.matches)
        setDuplicateOpen(true)
        return
      }
      await api.post('/sales/customers', payload)
      message.success('客户已创建')
      setCreateCustomerOpen(false)
      createCustomerForm.resetFields()
      await fetchCustomers()
      await fetchLinkAccounts()
    } finally {
      setCreateCustomerSubmitting(false)
    }
  }

  const saveNote = async (customerId: string, value: string) => {
    setSavingNoteCustomerId(customerId)
    try {
      const nextValue = value.trim() ? value : null
      await api.put(`/sales/customers/${customerId}`, { note: nextValue })
      setCustomers((prev) => prev.map((item) => (item.id === customerId ? { ...item, note: nextValue } : item)))
      setSavedNoteCustomerId(customerId)
      setTimeout(() => setSavedNoteCustomerId((curr) => (curr === customerId ? null : curr)), 1200)
    } finally {
      setSavingNoteCustomerId(null)
    }
  }

  const saveFollowUp = async (id: string, value: string | null, note?: string | null) => {
    await api.put(`/sales/customers/${id}`, { next_follow_up: value, note: note ?? undefined })
    fetchCustomers()
  }

  const addTag = async () => {
    if (!tagTarget || !selectedTag) return
    await api.post(`/sales/customers/${tagTarget.id}/tags`, { tag_id: selectedTag })
    message.success('标签已添加')
    setTagTarget(null)
    setSelectedTag(null)
    fetchCustomers()
  }

  const removeTag = async (customerId: string, tagId: string) => {
    await api.delete(`/sales/customers/${customerId}/tags/${tagId}`)
    fetchCustomers()
  }

  const addCourse = async () => {
    if (!courseTarget || !selectedProductId) return
    await api.post(`/sales/customers/${courseTarget.id}/courses`, { product_id: selectedProductId, amount: newCourseAmount ?? undefined })
    message.success('已购课程已新增，默认状态为已购未上')
    setCourseTarget(null)
    setSelectedProductId(null)
    setNewCourseAmount(null)
    fetchCustomers()
  }

  const submitGiftRequest = async () => {
    const values = await giftRequestForm.validateFields()
    await api.post('/sales/tuition-gift-requests', {
      customer_id: values.customer_id,
      amount: Number(values.amount_yuan),
      sales_note: values.sales_note || undefined,
    })
    message.success('赠送学费申请已提交')
    setGiftRequestOpen(false)
    giftRequestForm.resetFields()
    fetchGiftHistory()
  }

  const fetchGiftHistory = async () => {
    setGiftHistoryLoading(true)
    try {
      setGiftHistory(await api.get<TuitionGiftRequestItem[]>('/sales/tuition-gift-requests'))
    } finally {
      setGiftHistoryLoading(false)
    }
  }


  const updateCourseStatus = async (customerId: string, enrollmentId: string, status: CourseStatusKey) => {
    if (!salesStatusSet.has(status)) return
    const actionKey = `${customerId}:${enrollmentId}:status`
    setCourseActionLoadingKey(actionKey)
    try {
      await api.put(`/sales/customers/${customerId}/courses/${enrollmentId}/status`, { status })
      await fetchCustomers()
      setActiveCourseKey(null)
    } finally {
      setCourseActionLoadingKey(null)
    }
  }

  const updateCourseAmount = async (customerId: string, enrollmentId: string, amountPaid: number) => {
    const actionKey = `${customerId}:${enrollmentId}:amount`
    setCourseActionLoadingKey(actionKey)
    try {
      await api.put(`/sales/customers/${customerId}/courses/${enrollmentId}/amount`, { amount_paid: amountPaid })
      message.success('课程实付金额已更新')
      await fetchCustomers()
      setActiveCourseKey(null)
    } finally {
      setCourseActionLoadingKey(null)
    }
  }

  const refundCourse = async (customerId: string, enrollmentId: string, amount: number) => {
    const actionKey = `${customerId}:${enrollmentId}:refund`
    setCourseActionLoadingKey(actionKey)
    try {
      await api.post(`/sales/customers/${customerId}/courses/${enrollmentId}/refund`, { refund_amount: amount })
      message.success('退款成功')
      await fetchCustomers()
      setActiveCourseKey(null)
    } finally {
      setCourseActionLoadingKey(null)
    }
  }

  const revertRefundCourse = async (customerId: string, enrollmentId: string) => {
    const actionKey = `${customerId}:${enrollmentId}:revert`
    setCourseActionLoadingKey(actionKey)
    try {
      await api.post(`/sales/customers/${customerId}/courses/${enrollmentId}/refund/revert`, {})
      message.success('已撤销退款')
      await fetchCustomers()
      setActiveCourseKey(null)
    } finally {
      setCourseActionLoadingKey(null)
    }
  }

  useEffect(() => {
    const onDocMouseDown = (evt: MouseEvent) => {
      if (editingRefundCourse || editingAmountCourse) return
      if (!activeCourseKey || !activeCourseWrapRef.current) return
      if (!activeCourseWrapRef.current.contains(evt.target as Node)) {
        setActiveCourseKey(null)
      }
    }
    const onEsc = (evt: KeyboardEvent) => {
      if (evt.key === 'Escape') setActiveCourseKey(null)
    }
    document.addEventListener('mousedown', onDocMouseDown)
    document.addEventListener('keydown', onEsc)
    return () => {
      document.removeEventListener('mousedown', onDocMouseDown)
      document.removeEventListener('keydown', onEsc)
    }
  }, [activeCourseKey, editingRefundCourse, editingAmountCourse])

  const columns = [
    {
      title: '客户',
      width: 150,
      render: (_: unknown, r: Customer) => (
        <div>
          <div style={{ fontWeight: 700, fontSize: 13, lineHeight: 1.2 }}>{r.name}</div>
          <div style={{ fontSize: 11, color: '#8c8c8c', lineHeight: 1.2, marginTop: 2 }}>
            微信：{r.client_wechat_name || '-'}
          </div>
          {r.phone ? <div style={{ fontSize: 11, color: '#b0b0b0', lineHeight: 1.2, marginTop: 2 }}>手机：{r.phone}</div> : null}
        </div>
      ),
    },
    {
      title: '标签', width: 130,
      render: (_: unknown, r: Customer) => (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
          {r.tags.map((t) => (
            <Tag key={t.id} color={t.color} style={{ marginInlineEnd: 0, fontSize: 10, lineHeight: '14px', paddingInline: 6 }}>
              {t.name}
              <button onClick={() => removeTag(r.id, t.id)} style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'inherit', opacity: 0.6, padding: 0, marginLeft: 4, fontSize: 10 }}>×</button>
            </Tag>
          ))}
          <button onClick={() => { setTagTarget(r); setSelectedTag(null); fetchTags() }} style={{ border: '1px dashed #E8E8E3', background: 'none', cursor: 'pointer', color: '#8E8E8E', fontSize: 10, padding: '0 5px', borderRadius: 3, lineHeight: '14px' }}>+</button>
        </div>
      )
    },
    {
      title: '已购课程', width: 210,
      render: (_: unknown, r: Customer) => (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {r.courses.map((c) => {
            const meta = COURSE_STATUS_META[c.status as CourseStatusKey]
            const isRefunded = c.status.includes('refunded')
            const isAdminStatus = c.status.startsWith('admin_')
            const courseKey = `${r.id}:${c.enrollment_id}`
            const isActive = activeCourseKey === courseKey
            const maxRefund = Math.max(c.amount_paid - c.refunded_amount, 0)
            const nextStatus = (() => {
              if (isAdminStatus) return null
              if (c.status === 'purchased_not_started') return 'sales_marked_completed'
              if (c.status === 'sales_marked_completed') return 'purchased_not_started'
              if (c.status === 'purchased_not_started_refunded') return 'sales_marked_completed_refunded'
              if (c.status === 'sales_marked_completed_refunded') return 'purchased_not_started_refunded'
              return null
            })() as CourseStatusKey | null
            const toggleLabel = c.status.includes('sales_marked_completed') ? '回退未上' : '标记已上'
            return (
              <div key={c.enrollment_id} ref={isActive ? activeCourseWrapRef : null} style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 4 }}>
                <button
                  onClick={() => setActiveCourseKey((curr) => (curr === courseKey ? null : courseKey))}
                  style={{
                    border: `1px solid ${isActive ? '#3B82F6' : (meta?.border || '#d9d9d9')}`,
                    background: meta?.bg || '#f5f5f5',
                    color: meta?.color || '#595959',
                    borderRadius: 4,
                    padding: '1px 6px',
                    textAlign: 'left',
                    cursor: 'pointer',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    width: 'fit-content',
                    maxWidth: '100%',
                    fontSize: 11,
                  }}
                >
                  <span style={{ textDecoration: isRefunded ? 'line-through' : 'none', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 150 }}>{c.product_name}</span>
                </button>
                {isActive ? (
                  <div style={{ border: '1px solid #e5e7eb', background: '#fafafa', borderRadius: 6, padding: '5px 6px', width: '100%' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                      <span style={{ fontSize: 10, color: '#6b7280' }}>操作区</span>
                      <button onClick={() => setActiveCourseKey(null)} style={{ border: 'none', background: 'transparent', color: '#9ca3af', cursor: 'pointer', fontSize: 12, lineHeight: 1 }}>×</button>
                    </div>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                      {isAdminStatus ? (
                        <span style={{ fontSize: 10, color: '#6b7280' }}>管理员处理状态，销售不可修改</span>
                      ) : (
                        <>
                          {nextStatus ? (
                            <button
                              disabled={courseActionLoadingKey !== null}
                              onClick={() => void updateCourseStatus(r.id, c.enrollment_id, nextStatus)}
                              style={{ border: '1px solid #bfdbfe', background: '#eff6ff', color: '#1d4ed8', borderRadius: 4, fontSize: 10, padding: '0 6px', cursor: 'pointer' }}
                            >{toggleLabel}</button>
                          ) : null}
                          {!isRefunded ? (
                            <>
                              <button
                                disabled={courseActionLoadingKey !== null}
                                onClick={() => {
                                  setEditingAmountCourse({ customerId: r.id, enrollmentId: c.enrollment_id, amountPaid: c.amount_paid })
                                  amountForm.setFieldsValue({ amount_yuan: Number(c.amount_paid) })
                                }}
                                style={{ border: '1px solid #d9d9d9', background: '#fff', borderRadius: 4, fontSize: 10, padding: '0 6px', cursor: 'pointer' }}
                              >改价</button>
                              <button
                                disabled={maxRefund <= 0 || courseActionLoadingKey !== null}
                                onClick={() => {
                                  setEditingRefundCourse({ customerId: r.id, enrollmentId: c.enrollment_id, maxRefund })
                                  refundForm.setFieldsValue({ refund_yuan: Number(maxRefund) })
                                }}
                                style={{ border: '1px solid #fecaca', background: '#fff1f2', color: '#b91c1c', borderRadius: 4, fontSize: 10, padding: '0 6px', cursor: 'pointer' }}
                              >退款</button>
                            </>
                          ) : (
                            <button
                              disabled={courseActionLoadingKey !== null}
                              onClick={() => void revertRefundCourse(r.id, c.enrollment_id)}
                              style={{ border: '1px solid #bfdbfe', background: '#eff6ff', color: '#1d4ed8', borderRadius: 4, fontSize: 10, padding: '0 6px', cursor: 'pointer' }}
                            >撤销退款</button>
                          )}
                        </>
                      )}
                      <span style={{ fontSize: 10, color: '#6b7280' }}>{y2f(c.amount_paid)} / 已退 {y2f(c.refunded_amount)}</span>
                    </div>
                  </div>
                ) : null}
              </div>
            )
          })}
          <button onClick={() => { setCourseTarget(r); setSelectedProductId(null); fetchProducts() }} style={{ border: '1px dashed #E8E8E3', background: 'none', cursor: 'pointer', color: '#8E8E8E', fontSize: 10, padding: '0 5px', borderRadius: 3, width: 'fit-content', lineHeight: '14px' }}>+</button>
        </div>
      )
    },
    { title: '累计花费', width: 95, render: (_: unknown, r: Customer) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{y2f(r.total_spent)}</span> },
    { title: '学费结余', width: 95, render: (_: unknown, r: Customer) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: '#16A34A' }}>{y2f(r.tuition_balance)}</span> },
    {
      title: '备注', width: 160,
      render: (_: unknown, r: Customer) => {
        const isEditing = editingNoteCustomerId === r.id
        const isSaving = savingNoteCustomerId === r.id
        const isSaved = savedNoteCustomerId === r.id
        if (!isEditing) {
          return <div onClick={() => { setEditingNoteCustomerId(r.id); setNoteDraft(r.note || '') }} style={{ minHeight: 22, padding: '2px 6px', border: '1px solid transparent', borderRadius: 6, cursor: 'text', fontSize: 12, lineHeight: 1.4 }}>{r.note || <span style={{ color: '#bfbfbf' }}>点击填写备注</span>}</div>
        }
        return (
          <div>
            <Input.TextArea
              value={noteDraft}
              autoFocus
              autoSize={{ minRows: 1, maxRows: 3 }}
              onChange={(e) => setNoteDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  e.preventDefault(); setEditingNoteCustomerId(null); setNoteDraft(''); return
                }
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  saveNote(r.id, noteDraft).then(() => { setEditingNoteCustomerId(null); setNoteDraft('') })
                }
              }}
              onBlur={() => saveNote(r.id, noteDraft).then(() => { setEditingNoteCustomerId(null); setNoteDraft('') })}
            />
            <div style={{ marginTop: 4, color: '#8c8c8c', fontSize: 11 }}>
              Enter 保存　Esc 取消　{isSaving ? '保存中...' : isSaved ? <span style={{ color: '#389e0d' }}>已自动保存</span> : ''}
            </div>
          </div>
        )
      }
    },
    {
      title: '下次跟进', width: 100,
      render: (_: unknown, r: Customer) => {
        const color = r.next_follow_up_status === 'overdue' ? '#a8071a' : '#003a8c'
        const isEditingNext = editingNextCustomerId === r.id
        const lines = followUpLabel(r).split('\n')
        if (isEditingNext) {
          return (
            <DatePicker
              open
              autoFocus
              value={r.next_follow_up ? dayjs(r.next_follow_up) : null}
              showTime={{
                format: 'HH:00',
                disabledTime: () => ({
                  disabledMinutes: () => Array.from({ length: 59 }, (_, i) => i + 1),
                }),
              }}
              format="YYYY-MM-DD HH:00"
              style={{ width: 150 }}
              onChange={async (val) => {
                await saveFollowUp(r.id, val ? dayjs(val).startOf('hour').format('YYYY-MM-DDTHH:mm:ss') : null, r.note)
                setEditingNextCustomerId(null)
              }}
              onOpenChange={(open) => {
                if (!open) setEditingNextCustomerId(null)
              }}
            />
          )
        }
        return (
          <button
            onClick={() => setEditingNextCustomerId(r.id)}
            style={{ border: 'none', background: 'transparent', padding: 0, textAlign: 'left', cursor: 'pointer' }}
          >
            <div>
              <div style={{ color, fontWeight: 700, fontSize: 13, lineHeight: 1.2 }}>{lines[0]}</div>
              <div style={{ color, fontSize: 11, lineHeight: 1.2 }}>{lines[1] || ''}</div>
            </div>
          </button>
        )
      }
    },
    {
      title: '咨询次数',
      width: 90,
      render: (_: unknown, r: Customer) => (
        <button
          onClick={() => navigate(`/sales/customers/${r.id}/logs`)}
          style={{ border: '1px solid #d9d9d9', background: '#fff', borderRadius: 6, fontSize: 11, padding: '1px 8px', cursor: 'pointer' }}
        >
          {r.consultation_count ?? 0} 次
        </button>
      ),
    },
    { title: '加粉日期', width: 96, dataIndex: 'added_date', render: (v: string) => <span style={{ fontSize: 12 }}>{dayjs(v).format('YYYY-MM-DD')}</span> },
    { title: '绑定微信', width: 90, dataIndex: 'link_account_name', render: (v: string | null) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{v || '-'}</span> },
  ]

  return (
    <div>
      <div style={{ background: '#fff', border: '1px solid #e8e8e3', borderRadius: 10, padding: 14, marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <div style={{ fontWeight: 700, color: '#595959' }}>我的微信号</div>
          <Button onClick={() => { addWechatForm.resetFields(); setAddWechatOpen(true) }}>+ 新增微信号</Button>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button type={selectedLA === null ? 'primary' : 'default'} onClick={() => setSelectedLA(null)}>
            全部
            <span style={{ marginLeft: 6, fontSize: 11, color: selectedLA === null ? '#1D4ED8' : '#8E8E8E' }}>{totalCustomers}</span>
          </Button>
          {linkAccounts.map((la) => (
            <button key={la.id} onClick={() => setSelectedLA(la.id)} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, height: 32, padding: '0 12px', borderRadius: 8, border: `1px solid ${selectedLA === la.id ? '#BFDBFE' : '#D9D9D9'}`, background: selectedLA === la.id ? '#EAF2FF' : '#fff', cursor: 'pointer' }}>
              <span style={{ width: 6, height: 6, borderRadius: 3, background: la.is_active ? '#16A34A' : '#BFBFBF' }} />
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: '#111827' }}>{la.account_id}</span>
              <span style={{ fontSize: 10, lineHeight: '16px', padding: '0 6px', borderRadius: 10, color: selectedLA === la.id ? '#1D4ED8' : '#6B7280', background: selectedLA === la.id ? '#DBEAFE' : '#F3F4F6' }}>{la.customer_count}</span>
            </button>
          ))}
        </div>
      </div>

      <div style={{ background: '#fff', border: '1px solid #e8e8e3', borderRadius: 10, padding: 14, marginBottom: 14 }}>
        <div style={{ color: '#595959', fontSize: 13, marginBottom: 8 }}>已购课程标签的 6 种状态（仅展示）</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(220px, 1fr))', gap: 10 }}>
          {([
            'purchased_not_started',
            'sales_marked_completed',
            'admin_marked_completed',
            'purchased_not_started_refunded',
            'sales_marked_completed_refunded',
            'admin_marked_completed_refunded',
          ] as CourseStatusKey[]).map((key) => {
            const meta = COURSE_STATUS_META[key]
            const isRefunded = key.includes('refunded')
            return (
              <div
                key={key}
                style={{
                  textAlign: 'left',
                  borderRadius: 6,
                  border: `1px solid ${meta.border}`,
                  background: '#fff',
                  color: '#595959',
                  padding: '8px 10px',
                  cursor: 'default',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                }}
              >
                <span style={{ background: meta.bg, color: meta.color, border: `1px solid ${meta.border}`, borderRadius: 4, padding: '1px 8px', fontSize: 12, textDecoration: isRefunded ? 'line-through' : 'none' }}>
                  电商管理课
                </span>
                <span style={{ fontSize: 12, color: '#595959', textDecoration: isRefunded ? 'line-through' : 'none' }}>{meta.label}</span>
              </div>
            )
          })}
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 14 }}>
        <div><h2 style={{ marginBottom: 2 }}>我的客户</h2><div style={{ color: '#8c8c8c', fontSize: 12 }}>当前筛选共 {customers.length} 个客户</div></div>
        <div style={{ display: 'flex', gap: 10 }}>
          <Input.Search placeholder='搜索客户名/标签/备注' style={{ width: 260 }} value={keyword} onChange={(e) => setKeyword(e.target.value)} onSearch={() => fetchCustomers()} />
          <Button onClick={() => { setGiftRequestOpen(true); giftRequestForm.resetFields() }}>申请赠送学费</Button>
          <Button onClick={() => { setGiftHistoryOpen(true); void fetchGiftHistory() }}>申请记录</Button>
          <Button>批量导入</Button>
          <Button
            type='primary'
            onClick={() => {
              createCustomerForm.resetFields()
              createCustomerForm.setFieldsValue({ added_date: dayjs(), link_account_id: selectedLA ?? undefined })
              setCreateCustomerOpen(true)
            }}
          >
            + 新建客户
          </Button>
        </div>
      </div>

      <Table
        rowKey='id'
        dataSource={customers}
        columns={columns}
        loading={loading}
        pagination={false}
        size='small'
        tableLayout='fixed'
      />

      <Modal title='新增微信号' open={addWechatOpen} onOk={submitAddWechat} onCancel={() => setAddWechatOpen(false)}>
        <Form form={addWechatForm} layout='vertical'>
          <Form.Item name='account_id' label='微信号' rules={[{ required: true, message: '请输入微信号' }]}>
            <Input placeholder='例如 maoke_1234' />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title='发现重复客户'
        open={duplicateOpen}
        footer={null}
        onCancel={() => setDuplicateOpen(false)}
      >
        <div style={{ color: '#8c8c8c', marginBottom: 12 }}>客户微信号或手机号已存在，请先确认现有客户的归属销售和跟进顾问。</div>
        <div style={{ display: 'grid', gap: 10 }}>
          {duplicateMatches.map((item) => (
            <div key={item.customer_id} style={{ border: '1px solid #f0d9a6', background: '#fffaf0', borderRadius: 10, padding: 12 }}>
              <div style={{ fontWeight: 700 }}>{item.customer_name}</div>
              <div style={{ marginTop: 4, color: '#595959', fontSize: 12 }}>
                归属销售：{item.owner_name || '-'} · 绑定微信：{item.link_account_name || '-'}
              </div>
              <div style={{ marginTop: 4, color: '#595959', fontSize: 12 }}>
                跟进顾问：{item.consultant_names.length ? item.consultant_names.join('、') : '暂无'}
              </div>
              <div style={{ marginTop: 4, color: '#8c8c8c', fontSize: 12 }}>
                命中字段：{item.matched_fields.join('、')}
              </div>
              <div style={{ marginTop: 10, display: 'flex', justifyContent: 'flex-end' }}>
                <Button
                  type='primary'
                  onClick={() => {
                    setDuplicateOpen(false)
                    setCreateCustomerOpen(false)
                    navigate(`/sales/customers/${item.customer_id}/logs`)
                  }}
                >
                  打开现有客户
                </Button>
              </div>
            </div>
          ))}
        </div>
      </Modal>

      <Modal
        title='新建客户'
        open={createCustomerOpen}
        width={620}
        confirmLoading={createCustomerSubmitting}
        onOk={submitCreateCustomer}
        onCancel={() => setCreateCustomerOpen(false)}
      >
        <div style={{ background: 'linear-gradient(180deg, #f8fbff 0%, #ffffff 40%)', border: '1px solid #e6efff', borderRadius: 12, padding: 14 }}>
          <div style={{ fontSize: 12, color: '#3b82f6', marginBottom: 10 }}>客户基础信息</div>
          <Form form={createCustomerForm} layout='vertical'>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <Form.Item name='name' label='客户姓名' rules={[{ required: true, message: '请输入客户姓名' }]}>
                <Input maxLength={50} placeholder='例如：张三' />
              </Form.Item>
              <Form.Item name='phone' label='手机号（选填）'>
                <Input maxLength={20} placeholder='例如：13800000000' />
              </Form.Item>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <Form.Item name='client_wechat_name' label='客户微信号' rules={[{ required: true, message: '请输入客户微信号' }]}>
                <Input maxLength={100} placeholder='例如：zhangsan_01' />
              </Form.Item>
              <Form.Item name='industry' label='行业'>
                <Input maxLength={50} placeholder='选填' />
              </Form.Item>
              <Form.Item name='region' label='地区'>
                <Input maxLength={50} placeholder='选填' />
              </Form.Item>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <Form.Item name='link_account_id' label='绑定微信' rules={[{ required: true, message: '请选择微信号' }]}>
                <Select placeholder='选择微信号'>
                  {linkAccounts.map((la) => (
                    <Select.Option key={la.id} value={la.id}>
                      {la.account_id}
                    </Select.Option>
                  ))}
                </Select>
              </Form.Item>
              <Form.Item name='added_date' label='加粉日期' rules={[{ required: true, message: '请选择加粉日期' }]}>
                <DatePicker style={{ width: '100%' }} format='YYYY-MM-DD' />
              </Form.Item>
            </div>
            <Form.Item name='other_contact' label='其他联系方式（选填）' extra='支持填写客户微信号、QQ、邮箱等'>
              <Input maxLength={200} placeholder='例如：微信 zhangsan_01 / QQ 123456' />
            </Form.Item>
          </Form>
        </div>
      </Modal>

      <Modal
        title='发现重复客户'
        open={duplicateOpen}
        footer={null}
        onCancel={() => setDuplicateOpen(false)}
      >
        <div style={{ color: '#8c8c8c', marginBottom: 12 }}>客户微信号或手机号已存在，请先确认现有客户的归属销售和跟进顾问。</div>
        <div style={{ display: 'grid', gap: 10 }}>
          {duplicateMatches.map((item) => (
            <div key={item.customer_id} style={{ border: '1px solid #f0d9a6', background: '#fffaf0', borderRadius: 10, padding: 12 }}>
              <div style={{ fontWeight: 700 }}>{item.customer_name}</div>
              <div style={{ marginTop: 4, color: '#595959', fontSize: 12 }}>
                归属销售：{item.owner_name || '-'} · 绑定微信：{item.link_account_name || '-'}
              </div>
              <div style={{ marginTop: 4, color: '#595959', fontSize: 12 }}>
                跟进顾问：{item.consultant_names.length ? item.consultant_names.join('、') : '暂无'}
              </div>
              <div style={{ marginTop: 4, color: '#8c8c8c', fontSize: 12 }}>
                命中字段：{item.matched_fields.join('、')}
              </div>
              <div style={{ marginTop: 10, display: 'flex', justifyContent: 'flex-end' }}>
                <Button
                  type='primary'
                  onClick={() => {
                    setDuplicateOpen(false)
                    setCreateCustomerOpen(false)
                    navigate(`/sales/customers/${item.customer_id}/logs`)
                  }}
                >
                  打开现有客户
                </Button>
              </div>
            </div>
          ))}
        </div>
      </Modal>

      <Modal title='添加标签' open={!!tagTarget} onOk={addTag} onCancel={() => { setTagTarget(null); setSelectedTag(null) }}>
        <Select value={selectedTag} onChange={setSelectedTag} style={{ width: '100%' }} placeholder='搜索并选择标签' showSearch optionFilterProp='label'>
          {tagOptions.map((t) => (
            <Select.Option key={t.id} value={t.id} label={t.name}>
              <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: t.color, marginRight: 6 }} />
              {t.name}
              <span style={{ color: '#B8B8B8', fontSize: 10, marginLeft: 6 }}>{t.category_name}</span>
            </Select.Option>
          ))}
        </Select>
      </Modal>

      <Modal title='新增已购课程' open={!!courseTarget} onOk={addCourse} onCancel={() => { setCourseTarget(null); setSelectedProductId(null); setNewCourseAmount(null) }}>
        <div style={{ display: 'grid', gap: 10 }}>
        <Select value={selectedProductId} onChange={(v) => {
          setSelectedProductId(v)
          const p = productOptions.find((item) => item.id === v)
          setNewCourseAmount(p?.price ?? null)
        }} style={{ width: '100%' }} placeholder='选择课程'>
          {productOptions.filter((p) => p.status === 'active').map((p) => (
            <Select.Option key={p.id} value={p.id}>{p.name}（{y2f(p.price)}）</Select.Option>
          ))}
        </Select>
        <InputNumber
          min={0}
          precision={2}
          style={{ width: '100%' }}
          addonBefore='实付金额(元)'
          value={newCourseAmount === null ? null : Number(newCourseAmount)}
          onChange={(v) => setNewCourseAmount(v === null ? null : Number(v))}
        />
        </div>
      </Modal>

      <Modal
        title='申请赠送学费'
        open={giftRequestOpen}
        onOk={submitGiftRequest}
        onCancel={() => { setGiftRequestOpen(false); giftRequestForm.resetFields() }}
      >
        <Form form={giftRequestForm} layout='vertical'>
          <Form.Item name='customer_id' label='客户' rules={[{ required: true, message: '请选择客户' }]}>
            <Select showSearch optionFilterProp='label' placeholder='选择客户'>
              {customers.map((c) => <Select.Option key={c.id} value={c.id} label={`${c.name} ${c.phone || c.client_wechat_name || ''}`}>{c.name} {c.phone || c.client_wechat_name || '-'}</Select.Option>)}
            </Select>
          </Form.Item>
          <Form.Item name='amount_yuan' label='赠送学费(元)' rules={[{ required: true, message: '请输入金额' }, { type: 'number', min: 0.01, message: '金额必须大于0' }]}>
            <InputNumber min={0.01} precision={2} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name='sales_note' label='销售备注'>
            <Input.TextArea rows={3} placeholder='填写赠送原因或沟通备注' />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title='赠送学费申请记录'
        open={giftHistoryOpen}
        width={860}
        footer={null}
        onCancel={() => setGiftHistoryOpen(false)}
      >
        <Table
          rowKey='id'
          dataSource={giftHistory}
          loading={giftHistoryLoading}
          pagination={false}
          size='small'
          columns={[
            { title: '客户', dataIndex: 'customer_name', width: 120 },
            { title: '金额', dataIndex: 'amount', width: 110, render: (v: number) => y2f(v) },
            { title: '销售备注', dataIndex: 'sales_note', width: 220, render: (v: string | null) => v || '-' },
            { title: '审核备注', dataIndex: 'admin_note', width: 220, render: (v: string | null) => v || '-' },
            {
              title: '状态',
              dataIndex: 'status',
              width: 90,
              render: (v: TuitionGiftRequestItem['status']) => {
                if (v === 'approved') return <Tag color='green'>已通过</Tag>
                if (v === 'rejected') return <Tag color='red'>已驳回</Tag>
                return <Tag color='processing'>待处理</Tag>
              },
            },
            {
              title: '提交时间',
              dataIndex: 'created_at',
              width: 130,
              render: (v: string) => new Date(v).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
            },
            {
              title: '审核时间',
              dataIndex: 'reviewed_at',
              width: 130,
              render: (v: string | null) => (v ? new Date(v).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '-'),
            },
          ]}
        />
      </Modal>

      <Modal
        title='修改实付金额'
        open={!!editingAmountCourse}
        confirmLoading={courseActionLoadingKey?.endsWith(':amount')}
        onOk={async () => {
          if (!editingAmountCourse) return
          const values = await amountForm.validateFields()
          await updateCourseAmount(editingAmountCourse.customerId, editingAmountCourse.enrollmentId, Number(values.amount_yuan))
          setEditingAmountCourse(null)
          amountForm.resetFields()
        }}
        onCancel={() => { setEditingAmountCourse(null); amountForm.resetFields() }}
      >
        <Form form={amountForm} layout='vertical'>
          <Form.Item name='amount_yuan' label='实付金额(元)' rules={[{ required: true, message: '请输入实付金额' }, { type: 'number', min: 0, message: '金额不能小于0' }]}>
            <InputNumber min={0} precision={2} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title='课程退款'
        open={!!editingRefundCourse}
        confirmLoading={courseActionLoadingKey?.endsWith(':refund')}
        onOk={async () => {
          if (!editingRefundCourse) return
          const values = await refundForm.validateFields()
          const refundAmount = Number(values.refund_yuan)
          if (refundAmount > editingRefundCourse.maxRefund) {
            message.error('退款金额不能超过可退金额')
            return
          }
          await refundCourse(editingRefundCourse.customerId, editingRefundCourse.enrollmentId, refundAmount)
          setEditingRefundCourse(null)
          refundForm.resetFields()
        }}
        onCancel={() => { setEditingRefundCourse(null); refundForm.resetFields() }}
      >
        <Form form={refundForm} layout='vertical'>
          <Form.Item name='refund_yuan' label='退款金额(元)' rules={[{ required: true, message: '请输入退款金额' }, { type: 'number', min: 0.01, message: '退款金额必须大于0' }]}>
            <InputNumber min={0.01} precision={2} style={{ width: '100%' }} />
          </Form.Item>
          <div style={{ color: '#6b7280', fontSize: 12 }}>
            最多可退：{editingRefundCourse ? y2f(editingRefundCourse.maxRefund) : '¥0.00'}
          </div>
        </Form>
      </Modal>

    </div>
  )
}







