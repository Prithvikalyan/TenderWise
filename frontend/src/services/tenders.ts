export type TenderStatus = 'draft' | 'active' | 'archived'

export interface TenderCreate {
  name: string
  reference_number?: string | null
  issuing_organization?: string | null
  department?: string | null
  description?: string | null
}

export interface TenderUpdate {
  name?: string
  reference_number?: string | null
  issuing_organization?: string | null
  department?: string | null
  description?: string | null
  status?: TenderStatus
}

export interface Tender {
  id: string
  name: string
  reference_number: string | null
  issuing_organization: string | null
  department: string | null
  description: string | null
  status: TenderStatus
  created_at: string
  updated_at: string
}

export interface TenderDetail extends Tender {
  document_count: number
}

export interface TenderList {
  items: Tender[]
  total: number
  limit: number
  offset: number
}

export interface TenderListParams {
  limit: number
  offset: number
  search?: string
  status?: TenderStatus
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch {
    throw new Error('Could not reach the TenderWise API. Check that the backend is running.')
  }

  if (!response.ok) {
    let message = `Request failed (${response.status}).`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      // Keep the generic status message if the server did not return JSON.
    }
    throw new Error(message)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function listTenders(params: TenderListParams): Promise<TenderList> {
  const query = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) })
  if (params.search?.trim()) query.set('search', params.search.trim())
  if (params.status) query.set('status', params.status)
  return request<TenderList>(`/api/tenders?${query.toString()}`)
}

export function createTender(payload: TenderCreate): Promise<Tender> {
  return request<Tender>('/api/tenders', { method: 'POST', body: JSON.stringify(payload) })
}

export function getTender(id: string): Promise<TenderDetail> {
  return request<TenderDetail>(`/api/tenders/${encodeURIComponent(id)}`)
}

export function updateTender(id: string, payload: TenderUpdate): Promise<Tender> {
  return request<Tender>(`/api/tenders/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteTender(id: string): Promise<void> {
  return request<void>(`/api/tenders/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
