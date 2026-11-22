export const COMPANY_DOCUMENT_CATEGORIES = [
  'business_registration',
  'gst_certificate',
  'pan_card',
  'audited_financial_statements',
  'turnover_certificate',
  'purchase_order',
  'work_order',
  'completion_certificate',
  'non_blacklisting_declaration',
  'technical_certification',
  'license',
  'other',
] as const

export type CompanyDocumentCategory = (typeof COMPANY_DOCUMENT_CATEGORIES)[number]

export interface Company {
  id: string
  legal_name: string
  display_name: string | null
  registration_number: string | null
  notes: string | null
  is_archived: boolean
  created_at: string
  updated_at: string
}

export interface CompanyList {
  items: Company[]
  total: number
  limit: number
  offset: number
}

export interface CompanyDocumentVersion {
  id: string
  document_id: string
  version_number: number
  original_filename: string
  extension: string
  content_type: string
  size_bytes: number
  sha256: string | null
  issue_date: string | null
  expiry_date: string | null
  financial_year: string | null
  issuer: string | null
  reference_number: string | null
  notes: string | null
  created_at: string
}

export interface CompanyDocument {
  id: string
  company_id: string
  title: string
  category: CompanyDocumentCategory
  is_archived: boolean
  created_at: string
  updated_at: string
  current_version: CompanyDocumentVersion | null
  version_count: number
}

export interface CompanyDocumentList {
  items: CompanyDocument[]
  total: number
  limit: number
  offset: number
}

export interface CompanyDocumentVersionList {
  items: CompanyDocumentVersion[]
  total: number
}

export interface CompanyProcessingRunItem {
  id: string
  company_document_version_id: string | null
  status: 'pending' | 'processing' | 'completed' | 'failed'
  outcome_code: string | null
  failure_class: string | null
  attempt_count: number
  max_attempts: number
  safe_message: string | null
  extraction_unit_count: number
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface CompanyProcessingRun {
  id: string
  company_id: string
  status: 'pending' | 'processing' | 'partially_completed' | 'completed' | 'failed'
  requested_document_count: number
  created_at: string
  started_at: string | null
  completed_at: string | null
  updated_at: string
}

export interface CompanyProcessingRunCreated extends CompanyProcessingRun {
  items: CompanyProcessingRunItem[]
}

export interface CompanyProcessingRunDetail extends CompanyProcessingRun {
  item_counts: { pending: number; processing: number; completed: number; failed: number }
  items: CompanyProcessingRunItem[]
  total_items: number
  page: number
  limit: number
  offset: number
}

export interface CompanyProcessingRunList {
  items: CompanyProcessingRun[]
  total: number
  page: number
  limit: number
  offset: number
}

export interface CompanyExtractionUnit {
  id: string
  unit_index: number
  unit_type: string
  source_ref: Record<string, unknown>
  content_text: string | null
  table_data: unknown
  metadata: Record<string, unknown>
  created_at: string
}

export interface CompanyExtractionUnitList {
  run_id: string
  company_document_id: string
  company_document_version_id: string
  items: CompanyExtractionUnit[]
  total: number
  page: number
  limit: number
  offset: number
}

export interface CompanyCreatePayload {
  legal_name: string
  display_name?: string | null
  registration_number?: string | null
  notes?: string | null
}

export interface CompanyUploadPayload {
  file: File
  title: string
  category: CompanyDocumentCategory
  issue_date?: string
  expiry_date?: string
  financial_year?: string
  issuer?: string
  reference_number?: string
  notes?: string
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail)) {
      const first = body.detail[0] as { msg?: unknown } | undefined
      return typeof first?.msg === 'string' ? first.msg : 'The server rejected the request. Check the supplied values.'
    }
  } catch {
    // Use a status-based message if the server response is not JSON.
  }
  return `Request failed (${response.status}).`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...init?.headers,
      },
    })
  } catch {
    throw new Error('Could not reach the TenderWise API. Check that the backend is running.')
  }
  if (!response.ok) throw new Error(await readError(response))
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function companyPath(companyId: string): string {
  return `/api/companies/${encodeURIComponent(companyId)}`
}

export function listCompanies(params: { limit?: number; offset?: number; includeArchived?: boolean } = {}): Promise<CompanyList> {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 100),
    offset: String(params.offset ?? 0),
    include_archived: String(params.includeArchived ?? false),
  })
  return request<CompanyList>(`/api/companies?${query.toString()}`)
}

export function createCompany(payload: CompanyCreatePayload): Promise<Company> {
  return request<Company>('/api/companies', { method: 'POST', body: JSON.stringify(payload) })
}

export function listCompanyDocuments(
  companyId: string,
  params: { limit?: number; offset?: number; includeArchived?: boolean } = {},
): Promise<CompanyDocumentList> {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 100),
    offset: String(params.offset ?? 0),
    include_archived: String(params.includeArchived ?? false),
  })
  return request<CompanyDocumentList>(`${companyPath(companyId)}/documents?${query.toString()}`)
}

export function listCompanyDocumentVersions(companyId: string, documentId: string): Promise<CompanyDocumentVersionList> {
  return request<CompanyDocumentVersionList>(`${companyPath(companyId)}/documents/${encodeURIComponent(documentId)}/versions`)
}

export function archiveCompanyDocument(companyId: string, documentId: string): Promise<CompanyDocument> {
  return request<CompanyDocument>(
    `${companyPath(companyId)}/documents/${encodeURIComponent(documentId)}`,
    { method: 'PATCH', body: JSON.stringify({ is_archived: true }) },
  )
}

export function uploadCompanyDocument(companyId: string, payload: CompanyUploadPayload): Promise<CompanyDocument> {
  const form = new FormData()
  form.append('file', payload.file, payload.file.name)
  form.append('title', payload.title)
  form.append('category', payload.category)
  for (const key of ['issue_date', 'expiry_date', 'financial_year', 'issuer', 'reference_number', 'notes'] as const) {
    const value = payload[key]
    if (value?.trim()) form.append(key, value.trim())
  }
  return request<CompanyDocument>(`${companyPath(companyId)}/documents`, { method: 'POST', body: form })
}

export function updateCompanyDocumentVersionMetadata(
  companyId: string,
  documentId: string,
  versionId: string,
  payload: Partial<Pick<CompanyDocumentVersion, 'issue_date' | 'expiry_date' | 'financial_year' | 'issuer' | 'reference_number' | 'notes'>>,
): Promise<CompanyDocumentVersion> {
  return request<CompanyDocumentVersion>(
    `${companyPath(companyId)}/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}`,
    { method: 'PATCH', body: JSON.stringify(payload) },
  )
}

export function queueCompanyProcessingRun(companyId: string, versionIds: string[]): Promise<CompanyProcessingRunCreated> {
  return request<CompanyProcessingRunCreated>(`${companyPath(companyId)}/processing-runs`, {
    method: 'POST',
    body: JSON.stringify({ version_ids: versionIds }),
  })
}

export function listCompanyProcessingRuns(
  companyId: string,
  params: { page?: number; limit?: number } = {},
): Promise<CompanyProcessingRunList> {
  const query = new URLSearchParams({ page: String(params.page ?? 1), limit: String(params.limit ?? 10) })
  return request<CompanyProcessingRunList>(`${companyPath(companyId)}/processing-runs?${query.toString()}`)
}

export function getCompanyProcessingRun(companyId: string, runId: string): Promise<CompanyProcessingRunDetail> {
  return request<CompanyProcessingRunDetail>(`${companyPath(companyId)}/processing-runs/${encodeURIComponent(runId)}`)
}

export function getCompanyExtractionUnits(
  companyId: string,
  documentId: string,
  versionId: string,
): Promise<CompanyExtractionUnitList> {
  return request<CompanyExtractionUnitList>(
    `${companyPath(companyId)}/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/extraction-units?page=1&limit=500`,
  )
}
