export const DOCUMENT_CATEGORIES = [
  'main_tender',
  'technical_specification',
  'boq',
  'annexure_form',
  'corrigendum_amendment',
  'pre_bid_clarification',
  'supporting_document',
  'other',
] as const

export type DocumentCategory = (typeof DOCUMENT_CATEGORIES)[number]

export interface TenderDocument {
  id: string
  tender_id: string
  original_filename: string
  extension: string
  content_type: string
  size_bytes: number
  sha256: string | null
  category: DocumentCategory
  created_at: string
}

export interface DocumentList {
  items: TenderDocument[]
  total: number
  limit: number
  offset: number
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail)) return 'The server rejected the request. Check the selected files and categories.'
  } catch {
    // Use a safe status-based message when the response has no JSON body.
  }
  return `Request failed (${response.status}).`
}

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
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

function documentsPath(tenderId: string): string {
  return `/api/tenders/${encodeURIComponent(tenderId)}/documents`
}

export function listDocuments(
  tenderId: string,
  params: { limit: number; offset: number; category?: DocumentCategory },
): Promise<DocumentList> {
  const query = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) })
  if (params.category) query.set('category', params.category)
  return jsonRequest<DocumentList>(`${documentsPath(tenderId)}?${query.toString()}`)
}

export function uploadDocuments(
  tenderId: string,
  files: File[],
  categories: DocumentCategory[],
): Promise<TenderDocument[]> {
  if (files.length !== categories.length) throw new Error('Choose one category for every file.')
  const formData = new FormData()
  files.forEach((file, index) => {
    formData.append('files', file, file.name)
    formData.append('categories', categories[index])
  })
  return jsonRequest<TenderDocument[]>(documentsPath(tenderId), { method: 'POST', body: formData })
}

export function updateDocumentCategory(
  tenderId: string,
  documentId: string,
  category: DocumentCategory,
): Promise<TenderDocument> {
  return jsonRequest<TenderDocument>(`${documentsPath(tenderId)}/${encodeURIComponent(documentId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ category }),
  })
}

export async function downloadDocument(tenderId: string, documentId: string): Promise<Blob> {
  let response: Response
  try {
    response = await fetch(`${documentsPath(tenderId)}/${encodeURIComponent(documentId)}/download`)
  } catch {
    throw new Error('Could not reach the TenderWise API. Check that the backend is running.')
  }
  if (!response.ok) throw new Error(await readError(response))
  return response.blob()
}

export function deleteDocument(tenderId: string, documentId: string): Promise<void> {
  return jsonRequest<void>(`${documentsPath(tenderId)}/${encodeURIComponent(documentId)}`, { method: 'DELETE' })
}
