import { useEffect, useState } from 'react'
import { Button, DatePicker, Input, Pagination, Tag } from 'antd'
import dayjs from 'dayjs'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api/client'

interface LogItem {
  id: string
  customer_id: string
  consultant_id: string
  consultant_name: string
  log_date: string
  duration: number
  summary: string | null
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
  consultation_count: number
  total_duration: number
  latest_log_at: string | null
}

const PAGE_SIZE = 20

export default function SalesCustomerLogsPage() {
  const navigate = useNavigate()
  const { customerId = '' } = useParams()
  const [logs, setLogs] = useState<LogItem[]>([])
  const [detail, setDetail] = useState<DetailItem | null>(null)
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [dateRange, setDateRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null]>([null, null])
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(false)

  const fetchLogs = async (nextPage = page) => {
    if (!customerId) return
    setLoading(true)
    try {
      const q: string[] = [`page=${nextPage}`, `page_size=${PAGE_SIZE}`]
      if (keyword.trim()) q.push(`keyword=${encodeURIComponent(keyword.trim())}`)
      if (dateRange[0]) q.push(`date_from=${dateRange[0].format('YYYY-MM-DD')}`)
      if (dateRange[1]) q.push(`date_to=${dateRange[1].format('YYYY-MM-DD')}`)
      const [logRes, detailRes] = await Promise.all([
        api.get<LogItem[]>(`/consultant/customers/${customerId}/consultation-logs?${q.join('&')}`),
        api.get<DetailItem>(`/consultant/customers/${customerId}/detail`),
      ])
      setLogs(logRes)
      setDetail(detailRes)
      setHasMore(logRes.length >= PAGE_SIZE)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void fetchLogs(1) }, [customerId, dateRange])

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>客户咨询详情</h2>
          <p className="page-subtitle">只读视图：销售可查看历史咨询记录，不可新增或编辑</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <Button onClick={() => navigate('/sales/customers')}>返回</Button>
        </div>
      </div>

      {detail ? (
        <div style={{ background: '#fff', border: '1px solid #e8e8e3', borderRadius: 10, padding: 14, marginBottom: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
            <div>
              <div style={{ fontSize: 18, fontWeight: 700 }}>{detail.customer_name}</div>
              <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 2 }}>
                {detail.phone} · {detail.customer_info || '-'} · 销售：{detail.sales_name || '-'} · 微信：{detail.wechat_name || '-'}
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
              <div style={{ border: '1px solid #efefea', borderRadius: 8, padding: '8px 10px', minWidth: 160 }}>
                <div style={{ fontSize: 12, color: '#8c8c8c' }}>最近咨询</div>
                <div style={{ fontSize: 14, fontWeight: 700 }}>{detail.latest_log_at ? dayjs(detail.latest_log_at).format('M/D HH:mm') : '-'}</div>
              </div>
            </div>
          </div>
          <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {detail.tags.map((t) => <Tag key={t.id} color={t.color}>{t.name}</Tag>)}
          </div>
        </div>
      ) : null}

      <div style={{ marginBottom: 10, display: 'grid', gridTemplateColumns: '1fr 260px', gap: 8, alignItems: 'center' }}>
        <Input.Search
          allowClear
          value={keyword}
          placeholder='搜索摘要/内容'
          onChange={(e) => setKeyword(e.target.value)}
          onSearch={() => { setPage(1); void fetchLogs(1) }}
        />
        <DatePicker.RangePicker
          value={dateRange}
          onChange={(v) => {
            setPage(1)
            setDateRange([v?.[0] ?? null, v?.[1] ?? null])
          }}
          style={{ width: '100%' }}
        />
      </div>

      <div style={{ background: '#fff', border: '1px solid #e8e8e3', borderRadius: 10, padding: 16 }}>
        {loading ? '加载中...' : logs.length === 0 ? (
          <div style={{ color: '#8c8c8c' }}>暂无咨询日志</div>
        ) : logs.map((l) => (
          <div
            key={l.id}
            style={{
              border: '1px solid #ecebe6',
              borderRadius: 12,
              padding: 14,
              marginBottom: 10,
              background: '#fff',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <strong style={{ fontSize: 16 }}>{dayjs(l.log_date).format('M/D')}</strong>
                <span style={{ color: '#8c8c8c', fontSize: 13 }}>{l.duration} 分钟</span>
                <span style={{ background: '#f2f4f7', color: '#344054', borderRadius: 10, padding: '0 8px', fontSize: 11, lineHeight: '20px' }}>
                  {l.consultant_name}
                </span>
              </div>
              <Button
                size="small"
                onClick={() => navigate(`/sales/customers/${customerId}/logs/${l.id}`)}
              >
                查看详情
              </Button>
            </div>
            <div style={{ marginTop: 10, color: '#1f2937', lineHeight: 1.7 }}>{l.summary || '-'}</div>
            <div style={{ marginTop: 8, color: '#9ca3af', fontSize: 12 }}>更新于 {dayjs(l.updated_at).format('M/D HH:mm')}</div>
          </div>
        ))}
        <div style={{ marginTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
          <Pagination
            size='small'
            current={page}
            pageSize={PAGE_SIZE}
            total={hasMore ? page * PAGE_SIZE + 1 : (page - 1) * PAGE_SIZE + logs.length}
            showSizeChanger={false}
            onChange={(p) => { setPage(p); void fetchLogs(p) }}
          />
        </div>
      </div>
    </div>
  )
}
