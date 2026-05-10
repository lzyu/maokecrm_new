import { useEffect, useRef, useState } from 'react'
import { Button, DatePicker, Form, Input, InputNumber, Tag, message } from 'antd'
import dayjs from 'dayjs'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api/client'

interface LogDetailItem {
  id: string
  customer_id: string
  consultant_id: string
  consultant_name: string
  is_me: boolean
  editable: boolean
  log_date: string
  duration: number
  summary: string | null
  content: string | null
  created_at: string
  updated_at: string
}

interface DetailItem {
  customer_id: string
  customer_name: string
  phone: string
  customer_info: string
  sales_name: string | null
  wechat_name: string | null
  tags: { id: string; name: string; color: string }[]
  products: { product_id: string; product_name: string; is_refunded: boolean }[]
  consultation_count: number
  total_duration: number
  latest_log_at: string | null
}

interface DraftPayload {
  log_date: string | null
  duration: number | null
  summary: string | null
  content: string | null
}

const EDIT_DRAFT_PREFIX = 'consultant_log_draft_edit_'
const NEW_DRAFT_PREFIX = 'consultant_log_draft_new_'

export default function ConsultantLogEditorPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { customerId = '', logId = '' } = useParams()
  const [form] = Form.useForm()
  const [detail, setDetail] = useState<DetailItem | null>(null)
  const [logDetail, setLogDetail] = useState<LogDetailItem | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [draftSavedAt, setDraftSavedAt] = useState<string | null>(null)
  const initialSnapshotRef = useRef('')
  const isHydratingRef = useRef(false)

  const isSalesRoute = location.pathname.startsWith('/sales/')
  const basePath = isSalesRoute ? '/sales/customers' : '/consultant/customers'
  const isCreateMode = location.pathname.endsWith('/new')
  const isEditMode = location.pathname.endsWith('/edit')
  const isViewMode = isSalesRoute || (!isCreateMode && !isEditMode)
  const draftKey = isCreateMode ? `${NEW_DRAFT_PREFIX}${customerId}` : `${EDIT_DRAFT_PREFIX}${logId}`

  const makeSnapshot = (values: DraftPayload) => JSON.stringify(values)

  const getDraftPayload = (): DraftPayload => {
    const values = form.getFieldsValue()
    return {
      log_date: values.log_date ? dayjs(values.log_date).format('YYYY-MM-DD') : null,
      duration: values.duration ?? null,
      summary: values.summary ?? null,
      content: values.content ?? null,
    }
  }

  const applyDraft = (draft: DraftPayload) => {
    isHydratingRef.current = true
    form.setFieldsValue({
      log_date: draft.log_date ? dayjs(draft.log_date) : dayjs(),
      duration: draft.duration ?? 30,
      summary: draft.summary ?? '',
      content: draft.content ?? '',
    })
    window.setTimeout(() => {
      initialSnapshotRef.current = makeSnapshot(getDraftPayload())
      setDirty(false)
      isHydratingRef.current = false
    }, 0)
  }

  const loadPage = async () => {
    setLoading(true)
    try {
      const detailRes = await api.get<DetailItem>(`/consultant/customers/${customerId}/detail`)
      setDetail(detailRes)

      if (isCreateMode) {
        const raw = localStorage.getItem(draftKey)
        if (raw) {
          try {
            const draft = JSON.parse(raw) as DraftPayload
            applyDraft(draft)
            setDraftSavedAt('已恢复本地草稿')
          } catch {
            applyDraft({ log_date: dayjs().format('YYYY-MM-DD'), duration: 30, summary: '', content: '' })
          }
        } else {
          applyDraft({ log_date: dayjs().format('YYYY-MM-DD'), duration: 30, summary: '', content: '' })
        }
      } else {
        const logRes = await api.get<LogDetailItem>(`/consultant/logs/${logId}`)
        setLogDetail(logRes)
        if (isEditMode && !logRes.editable) {
          message.warning('这条日志不可编辑，已切换为查看模式')
          navigate(`${basePath}/${customerId}/logs/${logId}`, { replace: true })
          return
        }
        if (!isViewMode) {
          const raw = localStorage.getItem(draftKey)
          if (raw && window.confirm('检测到未提交草稿，是否恢复？')) {
            try {
              const draft = JSON.parse(raw) as DraftPayload
              applyDraft(draft)
              setDraftSavedAt('已恢复本地草稿')
            } catch {
              applyDraft({
                log_date: logRes.log_date,
                duration: logRes.duration,
                summary: logRes.summary,
                content: logRes.content,
              })
            }
          } else {
            applyDraft({
              log_date: logRes.log_date,
              duration: logRes.duration,
              summary: logRes.summary,
              content: logRes.content,
            })
          }
        }
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载日志详情失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (customerId) void loadPage()
  }, [customerId, logId, isCreateMode, isEditMode, isViewMode])

  useEffect(() => {
    if (isViewMode || !dirty) return
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [dirty, isViewMode])

  useEffect(() => {
    if (isViewMode) return
    const timer = window.setTimeout(() => {
      if (!dirty) return
      localStorage.setItem(draftKey, JSON.stringify(getDraftPayload()))
      setDraftSavedAt(`草稿已自动保存 ${dayjs().format('HH:mm:ss')}`)
    }, 1500)
    return () => window.clearTimeout(timer)
  }, [dirty, draftKey, isViewMode, form])

  const handleBack = () => {
    if (!isViewMode && dirty && !window.confirm('当前有未保存内容，确认离开吗？')) return
    navigate(`${basePath}/${customerId}/logs`)
  }

  const handleEdit = () => {
    navigate(`${basePath}/${customerId}/logs/${logId}/edit`)
  }

  const handleCancelEdit = () => {
    if (dirty && !window.confirm('当前有未保存内容，确认放弃修改吗？')) return
    if (isCreateMode) navigate(`${basePath}/${customerId}/logs`)
    else navigate(`${basePath}/${customerId}/logs/${logId}`)
  }

  const submit = async () => {
    const values = await form.validateFields()
    const payload = {
      log_date: dayjs(values.log_date).format('YYYY-MM-DD'),
      duration: values.duration,
      summary: values.summary ?? null,
      content: values.content ?? null,
    }
    setSaving(true)
    try {
      if (isCreateMode) {
        await api.post(`/consultant/customers/${customerId}/logs`, payload)
      } else {
        await api.put(`/consultant/logs/${logId}`, payload)
      }
      localStorage.removeItem(draftKey)
      setDirty(false)
      message.success('日志已保存')
      navigate(`${basePath}/${customerId}/logs`)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存日志失败')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div>加载中...</div>

  return (
    <div>
      <div className='page-header'>
        <div>
          <h2>{isCreateMode ? '新增咨询日志' : isEditMode ? '编辑咨询日志' : '咨询日志详情'}</h2>
          <p className='page-subtitle'>{isSalesRoute ? '销售只读查看咨询日志详情，和咨询侧保持一致的正文阅读体验' : '长内容写作独立成页，保留客户上下文与草稿能力'}</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {!isSalesRoute && isViewMode && logDetail?.editable ? <Button type='primary' onClick={handleEdit}>编辑</Button> : null}
          {!isViewMode ? <Button onClick={handleCancelEdit}>取消</Button> : null}
          {!isViewMode ? <Button type='primary' loading={saving} onClick={() => void submit()}>{isCreateMode ? '保存日志' : '保存修改'}</Button> : null}
          <Button onClick={handleBack}>{isViewMode ? '返回列表' : '稍后再说'}</Button>
        </div>
      </div>

      {detail ? (
        <div style={{ background: '#fff', border: '1px solid #e8e8e3', borderRadius: 10, padding: 16, marginBottom: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start' }}>
            <div>
              <div style={{ fontSize: 20, fontWeight: 700 }}>{detail.customer_name}</div>
              <div style={{ color: '#8c8c8c', fontSize: 12, marginTop: 4 }}>
                {detail.phone} · {detail.customer_info || '-'} · 销售：{detail.sales_name || '-'} · 微信：{detail.wechat_name || '-'}
              </div>
              <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {detail.tags.map((tag) => <Tag key={tag.id} color={tag.color}>{tag.name}</Tag>)}
                {detail.products.map((p) => (
                  <Tag
                    key={p.product_id}
                    style={{
                      textDecoration: p.is_refunded ? 'line-through' : 'none',
                      color: p.is_refunded ? '#cf1322' : '#135200',
                      borderColor: 'transparent',
                      background: p.is_refunded ? '#fff1f0' : '#e6fffb',
                    }}
                  >
                    {p.product_name}
                  </Tag>
                ))}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <div style={{ border: '1px solid #efefea', borderRadius: 8, padding: '8px 10px', minWidth: 110 }}>
                <div style={{ fontSize: 12, color: '#8c8c8c' }}>咨询次数</div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{detail.consultation_count}</div>
              </div>
              <div style={{ border: '1px solid #efefea', borderRadius: 8, padding: '8px 10px', minWidth: 130 }}>
                <div style={{ fontSize: 12, color: '#8c8c8c' }}>累计时长</div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{detail.total_duration} 分钟</div>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {draftSavedAt && !isViewMode ? (
        <div style={{ marginBottom: 12, color: '#389e0d', fontSize: 12 }}>{draftSavedAt}</div>
      ) : null}

      {isViewMode ? (
        <div style={{ background: '#fff', border: '1px solid #e8e8e3', borderRadius: 10, padding: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 12 }}>
            <div>
              <div style={{ fontSize: 22, fontWeight: 700 }}>{logDetail?.summary || '未填写摘要'}</div>
              <div style={{ color: '#8c8c8c', fontSize: 12, marginTop: 4 }}>
                {logDetail ? `${dayjs(logDetail.log_date).format('YYYY-MM-DD')} · ${logDetail.duration} 分钟 · ${logDetail.consultant_name}${logDetail.is_me ? '（我）' : ''}` : '-'}
              </div>
            </div>
            <div style={{ color: '#9ca3af', fontSize: 12 }}>
              {logDetail ? `更新于 ${dayjs(logDetail.updated_at).format('YYYY-MM-DD HH:mm')}` : ''}
            </div>
          </div>
          <div style={{ borderTop: '1px solid #f0f0f0', paddingTop: 16, whiteSpace: 'pre-wrap', lineHeight: 1.8, minHeight: 320 }}>
            {logDetail?.content?.trim() || '未填写内容'}
          </div>
        </div>
      ) : (
        <div style={{ background: '#fff', border: '1px solid #e8e8e3', borderRadius: 10, padding: 20 }}>
          <Form
            form={form}
            layout='vertical'
            onValuesChange={() => {
              if (isHydratingRef.current) return
              const snapshot = makeSnapshot(getDraftPayload())
              setDirty(snapshot !== initialSnapshotRef.current)
            }}
          >
            <div style={{ display: 'grid', gridTemplateColumns: '220px 220px 1fr', gap: 12 }}>
              <Form.Item name='log_date' label='日期' rules={[{ required: true }]}>
                <DatePicker style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name='duration' label='时长（分钟）' rules={[{ required: true }]}>
                <InputNumber min={1} style={{ width: '100%' }} />
              </Form.Item>
              <div style={{ display: 'flex', gap: 8, alignItems: 'end', paddingBottom: 24 }}>
                {[15, 30, 45, 60, 90].map((minutes) => (
                  <Button key={minutes} size='small' onClick={() => form.setFieldValue('duration', minutes)}>
                    {minutes}分钟
                  </Button>
                ))}
              </div>
            </div>
            <Form.Item name='summary' label='摘要' rules={[{ max: 60, message: '摘要最多 60 字' }]}>
              <Input placeholder='一句话总结本次咨询重点，便于列表快速检索' />
            </Form.Item>
            <Form.Item
              name='content'
              label='内容'
              rules={[{
                validator: async (_, value: string | undefined) => {
                  const text = (value || '').trim()
                  if (text.length > 0 && text.length < 10) throw new Error('内容至少 10 字，或留空')
                },
              }]}
              extra='支持自动草稿保存；长内容建议在这里完整整理咨询过程、问题诊断、动作建议和后续跟进。'
            >
              <Input.TextArea rows={18} showCount maxLength={5000} placeholder='建议按「客户现状 / 问题判断 / 方案建议 / 下次跟进」结构记录' />
            </Form.Item>
          </Form>
        </div>
      )}
    </div>
  )
}
