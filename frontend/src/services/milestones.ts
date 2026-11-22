export const MILESTONE_CATEGORIES = [
  'submission_deadline',
  'clarification_deadline',
  'pre_bid_meeting',
  'bid_opening',
  'emd_deadline',
  'document_deadline',
  'other',
] as const

export type MilestoneCategory = (typeof MILESTONE_CATEGORIES)[number]

export interface TenderMilestone {
  id: string
  tender_id: string
  tender_name: string
  tender_reference: string | null
  title: string
  category: MilestoneCategory
  due_date: string
  notes: string | null
  source_reference: string | null
  is_completed: boolean
  created_at: string
  updated_at: string
}

export interface MilestoneCreate {
  title: string
  category: MilestoneCategory
  due_date: string
  notes?: string | null
  source_reference?: string | null
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      headers: { ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
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
      // Keep the status message if the API response is not JSON.
    }
    throw new Error(message)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function listMilestones(params: { fromDate: string; toDate: string; includeCompleted?: boolean }) {
  const query = new URLSearchParams({ from_date: params.fromDate, to_date: params.toDate })
  if (params.includeCompleted === false) query.set('include_completed', 'false')
  return request<{ items: TenderMilestone[]; total: number }>(`/api/milestones?${query.toString()}`)
}

export function createMilestone(tenderId: string, payload: MilestoneCreate) {
  return request<TenderMilestone>(`/api/tenders/${encodeURIComponent(tenderId)}/milestones`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateMilestone(milestoneId: string, payload: { is_completed?: boolean; title?: string; due_date?: string; notes?: string | null }) {
  return request<TenderMilestone>(`/api/milestones/${encodeURIComponent(milestoneId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteMilestone(milestoneId: string) {
  return request<void>(`/api/milestones/${encodeURIComponent(milestoneId)}`, { method: 'DELETE' })
}
