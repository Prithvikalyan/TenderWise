import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { listDocuments, uploadDocuments, type DocumentList, type TenderDocument } from './services/documents'
import { createMilestone, listMilestones, updateMilestone } from './services/milestones'
import {
  createTender,
  deleteTender,
  getTender,
  listTenders,
  updateTender,
  type Tender,
  type TenderDetail,
  type TenderList,
} from './services/tenders'

vi.mock('./services/tenders', () => ({
  createTender: vi.fn(),
  deleteTender: vi.fn(),
  getTender: vi.fn(),
  listTenders: vi.fn(),
  updateTender: vi.fn(),
}))

vi.mock('./services/documents', () => ({
  deleteDocument: vi.fn(),
  downloadDocument: vi.fn(),
  listDocuments: vi.fn(),
  updateDocumentCategory: vi.fn(),
  uploadDocuments: vi.fn(),
  DOCUMENT_CATEGORIES: ['main_tender', 'technical_specification', 'boq', 'annexure_form', 'corrigendum_amendment', 'pre_bid_clarification', 'supporting_document', 'other'],
}))

vi.mock('./services/milestones', () => ({
  createMilestone: vi.fn(),
  listMilestones: vi.fn(),
  updateMilestone: vi.fn(),
  deleteMilestone: vi.fn(),
  MILESTONE_CATEGORIES: ['submission_deadline', 'clarification_deadline', 'pre_bid_meeting', 'bid_opening', 'emd_deadline', 'document_deadline', 'other'],
}))

const sampleTender: Tender = {
  id: '4ba1f42a-4fcb-4fca-bf2f-60f26d92e994',
  name: 'Road resurfacing works',
  reference_number: 'PWD-2026-14',
  issuing_organization: 'Public Works Department',
  department: 'Roads Division',
  description: 'Resurfacing works for the district road network.',
  status: 'draft',
  created_at: '2026-04-14T10:00:00Z',
  updated_at: '2026-04-15T12:30:00Z',
}

const sampleDetail: TenderDetail = { ...sampleTender, document_count: 0 }

function listResult(items: Tender[], total = items.length, offset = 0): TenderList {
  return { items, total, limit: 10, offset }
}

beforeEach(() => {
  vi.clearAllMocks()
  window.history.replaceState({}, '', '/')
  Object.defineProperty(window, 'scrollTo', { value: vi.fn(), configurable: true })
  vi.mocked(listTenders).mockResolvedValue(listResult([]))
  vi.mocked(createTender).mockResolvedValue(sampleTender)
  vi.mocked(getTender).mockResolvedValue(sampleDetail)
  vi.mocked(updateTender).mockResolvedValue(sampleTender)
  vi.mocked(deleteTender).mockResolvedValue(undefined)
  vi.mocked(listDocuments).mockResolvedValue({ items: [], total: 0, limit: 8, offset: 0 })
  vi.mocked(uploadDocuments).mockResolvedValue([])
  vi.mocked(listMilestones).mockResolvedValue({ items: [], total: 0 })
  vi.mocked(createMilestone).mockResolvedValue({
    id: 'milestone-1', tender_id: sampleTender.id, tender_name: sampleTender.name,
    tender_reference: sampleTender.reference_number, title: 'Bid submission closes',
    category: 'submission_deadline', due_date: '2026-12-15', notes: null,
    source_reference: null, is_completed: false, created_at: '2026-11-22T10:00:00Z',
    updated_at: '2026-11-22T10:00:00Z',
  })
  vi.mocked(updateMilestone).mockImplementation(async (_id, payload) => ({
    id: 'milestone-1', tender_id: sampleTender.id, tender_name: sampleTender.name,
    tender_reference: sampleTender.reference_number, title: 'Bid submission closes',
    category: 'submission_deadline', due_date: '2026-12-15', notes: null,
    source_reference: null, is_completed: payload.is_completed ?? false,
    created_at: '2026-11-22T10:00:00Z', updated_at: '2026-11-22T10:00:00Z',
  }))
})

afterEach(() => cleanup())

describe('TenderWise dashboard and workspace', () => {
  it('loads, searches, filters and paginates real tender list responses', async () => {
    const user = userEvent.setup()
    vi.mocked(listTenders).mockResolvedValue(listResult([sampleTender], 12))
    render(<App />)

    expect(await screen.findByText('Road resurfacing works')).toBeTruthy()
    expect(listTenders).toHaveBeenCalledWith({ limit: 10, offset: 0, search: '', status: undefined })

    await user.type(screen.getByRole('searchbox', { name: 'Search tenders' }), 'resurfacing')
    await waitFor(() => {
      expect(listTenders).toHaveBeenLastCalledWith({
        limit: 10,
        offset: 0,
        search: 'resurfacing',
        status: undefined,
      })
    })

    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by status' }), 'active')
    await waitFor(() => {
      expect(listTenders).toHaveBeenLastCalledWith({
        limit: 10,
        offset: 0,
        search: 'resurfacing',
        status: 'active',
      })
    })

    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(listTenders).toHaveBeenLastCalledWith({
      limit: 10,
      offset: 10,
      search: 'resurfacing',
      status: 'active',
    }))
  })

  it('creates a tender, refreshes the list and opens the new workspace', async () => {
    const user = userEvent.setup()
    vi.mocked(createTender).mockResolvedValue({ ...sampleTender, name: 'Bridge renewal' })
    vi.mocked(getTender).mockResolvedValue({ ...sampleDetail, name: 'Bridge renewal' })
    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'New tender' }))
    await user.type(screen.getByRole('textbox', { name: 'Tender name' }), 'Bridge renewal')
    await user.type(screen.getByRole('textbox', { name: 'Reference number' }), 'BR-401')
    await user.click(screen.getByRole('button', { name: 'Create workspace' }))

    expect(await screen.findByRole('heading', { name: 'Bridge renewal' })).toBeTruthy()
    expect(createTender).toHaveBeenCalledWith({
      name: 'Bridge renewal',
      reference_number: 'BR-401',
      issuing_organization: null,
      department: null,
      description: null,
    })
    expect(listTenders).toHaveBeenCalledWith({ limit: 100, offset: 0 })
    expect(listTenders).toHaveBeenCalledWith({ limit: 10, offset: 0, search: '', status: undefined })
    expect(getTender).toHaveBeenCalledWith(sampleTender.id)
    expect(await screen.findByText('Tender “Bridge renewal” created.')).toBeTruthy()
  })

  it('edits a tender and deletes it only after confirmation', async () => {
    const user = userEvent.setup()
    vi.mocked(listTenders).mockResolvedValue(listResult([sampleTender]))
    vi.mocked(updateTender).mockResolvedValue({ ...sampleTender, name: 'Updated resurfacing works', status: 'active' })
    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Open workspace' }))
    expect(await screen.findByRole('heading', { name: 'Road resurfacing works' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Edit details' }))

    const editDialog = screen.getByRole('dialog', { name: 'Edit tender details' })
    const nameInput = within(editDialog).getByRole('textbox', { name: 'Tender name' })
    await user.clear(nameInput)
    await user.type(nameInput, 'Updated resurfacing works')
    await user.selectOptions(within(editDialog).getByRole('combobox', { name: 'Status' }), 'active')
    await user.click(within(editDialog).getByRole('button', { name: 'Save changes' }))

    expect(await screen.findByRole('heading', { name: 'Updated resurfacing works' })).toBeTruthy()
    expect(updateTender).toHaveBeenCalledWith(sampleTender.id, expect.objectContaining({
      name: 'Updated resurfacing works',
      status: 'active',
    }))

    await user.click(screen.getByRole('button', { name: 'Delete tender' }))
    const confirmDialog = screen.getByRole('alertdialog', { name: 'Delete this tender?' })
    expect(within(confirmDialog).getByText(/cannot be undone/i)).toBeTruthy()
    await user.click(within(confirmDialog).getByRole('button', { name: 'Delete tender' }))

    expect(await screen.findByRole('heading', { name: 'Tender workspaces' })).toBeTruthy()
    expect(deleteTender).toHaveBeenCalledWith(sampleTender.id)
  })

  it('opens each analysis and Q&A in its own deep-linkable workspace view', async () => {
    const user = userEvent.setup()
    window.history.replaceState({}, '', `/tenders/${sampleTender.id}`)
    render(<App />)

    await screen.findByRole('heading', { name: 'Road resurfacing works' })
    const workspaceNav = screen.getByRole('navigation', { name: 'Tender workspace navigation' })
    await user.click(within(workspaceNav).getByRole('button', { name: 'Tender analysis' }))
    expect(await screen.findByRole('heading', { name: 'Tender analysis', level: 2 })).toBeTruthy()
    expect(window.location.search).toBe('?view=tender-analysis')

    await user.click(within(workspaceNav).getByRole('button', { name: 'Eligibility analysis' }))
    expect(await screen.findByRole('heading', { name: 'Eligibility analysis', level: 2 })).toBeTruthy()
    expect(window.location.search).toBe('?view=eligibility-analysis')

    await user.click(within(workspaceNav).getByRole('button', { name: 'Compliance analysis' }))
    expect(await screen.findByRole('heading', { name: 'Compliance analysis', level: 2 })).toBeTruthy()
    expect(window.location.search).toBe('?view=compliance-analysis')

    await user.click(within(workspaceNav).getByRole('button', { name: 'Tender Q&A' }))
    expect(await screen.findByRole('heading', { name: 'Ask questions about this tender' })).toBeTruthy()
    expect(window.location.search).toBe('?view=qa')
  })

  it('creates a date-confirmed milestone from the dashboard calendar', async () => {
    const user = userEvent.setup()
    vi.mocked(listTenders).mockResolvedValue(listResult([sampleTender]))
    render(<App />)

    await screen.findByText('Road resurfacing works')
    await user.click(screen.getByRole('button', { name: '+ Add milestone' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add a tender milestone' })
    await user.type(within(dialog).getByRole('textbox', { name: 'Milestone title *' }), 'Bid submission closes')
    await user.selectOptions(within(dialog).getByLabelText('Tender workspace *'), sampleTender.id)
    fireEvent.change(within(dialog).getByLabelText('Date *'), { target: { value: '2026-12-15' } })
    await user.type(within(dialog).getByLabelText('Source reference (optional)'), 'Tender notice, page 8')
    await user.click(within(dialog).getByRole('button', { name: 'Save milestone' }))

    await waitFor(() => expect(createMilestone).toHaveBeenCalledWith(sampleTender.id, expect.objectContaining({
      title: 'Bid submission closes', category: 'submission_deadline', due_date: '2026-12-15',
      source_reference: 'Tender notice, page 8',
    })))
    expect(listMilestones).toHaveBeenCalled()
  })

  it('compares two selected tender workspaces using returned backend details', async () => {
    const user = userEvent.setup()
    const secondTender: Tender = { ...sampleTender, id: 'tender-2', name: 'Bridge replacement', reference_number: 'BR-2026-03' }
    vi.mocked(listTenders).mockResolvedValue(listResult([sampleTender, secondTender]))
    vi.mocked(getTender).mockImplementation(async (id) => ({
      ...(id === 'tender-2' ? secondTender : sampleTender),
      document_count: id === 'tender-2' ? 4 : 2,
    }))
    render(<App />)

    await screen.findByText('Road resurfacing works')
    await user.click(screen.getByRole('checkbox', { name: 'Select Road resurfacing works for comparison' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select Bridge replacement for comparison' }))
    await user.click(screen.getByRole('button', { name: 'Compare (2/3)' }))

    const dialog = await screen.findByRole('dialog', { name: 'Compare selected tenders' })
    expect(within(dialog).getByText('Uploaded documents')).toBeTruthy()
    expect(within(dialog).getByText('2')).toBeTruthy()
    expect(within(dialog).getByText('4')).toBeTruthy()
    expect(getTender).toHaveBeenCalledWith(sampleTender.id)
    expect(getTender).toHaveBeenCalledWith('tender-2')
    expect(within(dialog).getByText(/does not infer technical, financial or eligibility equivalence/i)).toBeTruthy()
  })

  it('loads real documents and refreshes the backend document count after an upload', async () => {
    const user = userEvent.setup()
    const document: TenderDocument = {
      id: 'doc-1',
      tender_id: sampleTender.id,
      original_filename: 'notice.pdf',
      extension: '.pdf',
      content_type: 'application/pdf',
      size_bytes: 1024,
      sha256: 'hash',
      category: 'main_tender',
      created_at: '2026-04-14T10:00:00Z',
    }
    const empty: DocumentList = { items: [], total: 0, limit: 8, offset: 0 }
    const loaded: DocumentList = { items: [document], total: 1, limit: 8, offset: 0 }
    window.history.replaceState({}, '', `/tenders/${sampleTender.id}`)
    vi.mocked(getTender).mockResolvedValueOnce(sampleDetail).mockResolvedValueOnce({ ...sampleDetail, document_count: 1 })
    vi.mocked(listDocuments).mockResolvedValue(empty)
    vi.mocked(uploadDocuments).mockResolvedValue([document])
    render(<App />)

    await screen.findByRole('heading', { name: 'Road resurfacing works' })
    await user.click(within(screen.getByRole('navigation', { name: 'Tender workspace navigation' })).getByRole('button', { name: 'Documents' }))
    await screen.findByLabelText('Choose tender documents')
    vi.mocked(listDocuments).mockResolvedValue(loaded)
    await user.upload(screen.getByLabelText('Choose tender documents'), new File(['pdf'], 'notice.pdf', { type: 'application/pdf' }))
    await user.click(screen.getByRole('button', { name: 'Upload selected' }))
    expect(await screen.findByText('notice.pdf')).toBeTruthy()
    expect(await screen.findByLabelText('1 documents')).toBeTruthy()
    expect(uploadDocuments).toHaveBeenCalledWith(sampleTender.id, expect.any(Array), ['main_tender'])
  })
})
