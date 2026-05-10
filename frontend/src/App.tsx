import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import LoginPage from './pages/login/LoginPage'
import SalesLayout from './layouts/SalesLayout'
import ConsultantLayout from './layouts/ConsultantLayout'
import AdminLayout from './layouts/AdminLayout'
import ProtectedRoute from './components/ProtectedRoute'
import ProductsPage from './pages/admin/ProductsPage'
import TagsPage from './pages/admin/TagsPage'
import PersonnelPage from './pages/admin/PersonnelPage'
import LinkAccountsPage from './pages/admin/LinkAccountsPage'
import AdminPoolPage from './pages/admin/AdminPoolPage'
import AdminCustomersPage from './pages/admin/AdminCustomersPage'
import AdminDataReviewPage from './pages/admin/AdminDataReviewPage'
import AdminTuitionAndWriteoffPage from './pages/admin/AdminTuitionAndWriteoffPage'
import AdminAuditLogsPage from './pages/admin/AdminAuditLogsPage'
import CustomerList from './pages/sales/CustomerList'
import DataReview from './pages/sales/DataReview'
import SalesCustomerLogsPage from './pages/sales/SalesCustomerLogsPage'
import ConsultantCustomersPage from './pages/consultant/ConsultantCustomersPage'
import ConsultantDataReviewPage from './pages/consultant/ConsultantDataReviewPage'
import ConsultantPoolPage from './pages/consultant/ConsultantPoolPage'
import ConsultantCustomerLogsPage from './pages/consultant/ConsultantCustomerLogsPage'
import ConsultantLogEditorPage from './pages/consultant/ConsultantLogEditorPage'

export default function App() {
  return (
    <ConfigProvider locale={zhCN}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />

          <Route element={<ProtectedRoute allowedRoles={['sales']} />}>
            <Route element={<SalesLayout />}>
              <Route path="/sales/customers" element={<CustomerList />} />
              <Route path="/sales/customers/:customerId/logs" element={<SalesCustomerLogsPage />} />
              <Route path="/sales/customers/:customerId/logs/:logId" element={<ConsultantLogEditorPage />} />
              <Route path="/sales/data-review" element={<DataReview />} />
            </Route>
          </Route>

          <Route element={<ProtectedRoute allowedRoles={['consultant']} />}>
            <Route element={<ConsultantLayout />}>
              <Route path="/consultant/customers" element={<ConsultantCustomersPage />} />
              <Route path="/consultant/customers/:customerId/logs" element={<ConsultantCustomerLogsPage />} />
              <Route path="/consultant/customers/:customerId/logs/new" element={<ConsultantLogEditorPage />} />
              <Route path="/consultant/customers/:customerId/logs/:logId" element={<ConsultantLogEditorPage />} />
              <Route path="/consultant/customers/:customerId/logs/:logId/edit" element={<ConsultantLogEditorPage />} />
              <Route path="/consultant/data-review" element={<ConsultantDataReviewPage />} />
              <Route path="/consultant/pool" element={<ConsultantPoolPage />} />
            </Route>
          </Route>

          <Route element={<ProtectedRoute allowedRoles={['admin']} />}>
            <Route element={<AdminLayout />}>
              <Route path="/admin/data-review" element={<AdminDataReviewPage />} />
              <Route path="/admin/personnel" element={<PersonnelPage />} />
              <Route path="/admin/link-accounts" element={<LinkAccountsPage />} />
              <Route path="/admin/customers" element={<AdminCustomersPage />} />
              <Route path="/admin/pool" element={<AdminPoolPage />} />
              <Route path="/admin/tags" element={<TagsPage />} />
              <Route path="/admin/products" element={<ProductsPage />} />
              <Route path="/admin/tuition-writeoff" element={<AdminTuitionAndWriteoffPage />} />
              <Route path="/admin/audit-logs" element={<AdminAuditLogsPage />} />
            </Route>
          </Route>

          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  )
}
