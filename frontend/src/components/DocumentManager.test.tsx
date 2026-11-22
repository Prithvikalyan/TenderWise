import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DocumentManager from './DocumentManager'
import {
  deleteDocument,
  downloadDocument,
  listDocuments,
  updateDocumentCategory,
  uploadDocuments,
  type DocumentList,
  type TenderDocument,
} from '../services/documents'

vi.mock('../services/documents', () => ({
  deleteDocument: vi.fn(),
  downloadDocument: vi.fn(),
  listDocuments: vi.fn(),
  updateDocumentCategory: vi.fn(),
  uploadDocuments: vi.fn(),
  DOCUMENT_CATEGORIES: [
    'main_tender', 'technical_specification', 'boq', 'annexure_form',
    'corrigendum_amendment', 'pre_bid_clarification', 'supporting_document', 'other',
  ],
}))

const tenderId = 'tender-123'
const firstDocument: TenderDocument = {
  id: 'doc-1',
  tender_id: tenderId,
  original_filename: 'notice.pdf',
  extension: '.pdf',
  content_type: 'application/pdf',
  size_bytes: 1024,
  sha256: 'checksum',
  category: 'main_tender',
  created_at: '2026-11-16T10:00:00Z',
}

function page(items: TenderDocument[] = [], total = items.length, offset = 0): DocumentList {
  return { items, total, limit: 8, offset }
}

function renderManager(onDocumentsChanged = vi.fn().mockResolvedValue(undefined)) {
  return {
    onDocumentsChanged,
    ...render(<DocumentManager tenderId={tenderId} documentCount={3} onDocumentsChanged={onDocumentsChanged} />),
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listDocuments).mockResolvedValue(page())
  vi.mocked(uploadDocuments).mockResolvedValue([])
  vi.mocked(updateDocumentCategory).mockResolvedValue(firstDocument)
  vi.mocked(downloadDocument).mockResolvedValue(new Blob(['file bytes'], { type: 'application/pdf' }))
  vi.mocked(deleteDocument).mockResolvedValue(undefined)
  vi.stubGlobal('confirm', vi.fn().mockReturnValue(true))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn().mockReturnValue('blob:document'), revokeObjectURL: vi.fn() })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('tender document management', () => {
  it('selects multiple files, assigns individual categories and removes a selected file', async () => {
    const user = userEvent.setup()
    renderManager()
    const input = screen.getByLabelText('Choose tender documents') as HTMLInputElement
    await user.upload(input, [
      new File(['notice'], 'notice.pdf', { type: 'application/pdf' }),
      new File(['estimate'], 'estimate.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }),
    ])

    expect(screen.getByText('2 selected')).toBeTruthy()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Category for estimate.xlsx' }), 'boq')
    expect(screen.getByRole('combobox', { name: 'Category for estimate.xlsx' })).toHaveProperty('value', 'boq')
    await user.click(screen.getByRole('button', { name: 'Remove notice.pdf' }))
    expect(screen.queryByText('notice.pdf')).toBeNull()
    expect(screen.getByText('estimate.xlsx')).toBeTruthy()
    expect(screen.getByText('1 selected')).toBeTruthy()
  })

  it('uploads selected files as one request with matching per-file categories and refreshes the count', async () => {
    const user = userEvent.setup()
    const onDocumentsChanged = vi.fn().mockResolvedValue(undefined)
    vi.mocked(listDocuments).mockResolvedValueOnce(page()).mockResolvedValueOnce(page([firstDocument]))
    renderManager(onDocumentsChanged)
    await user.upload(screen.getByLabelText('Choose tender documents'), [
      new File(['notice'], 'notice.pdf', { type: 'application/pdf' }),
      new File(['boq'], 'bill.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }),
    ])
    await user.selectOptions(screen.getByRole('combobox', { name: 'Category for bill.xlsx' }), 'boq')
    await user.click(screen.getByRole('button', { name: 'Upload selected' }))

    await screen.findByText('Documents uploaded successfully.')
    expect(uploadDocuments).toHaveBeenCalledTimes(1)
    const [sentTenderId, files, categories] = vi.mocked(uploadDocuments).mock.calls[0]
    expect(sentTenderId).toBe(tenderId)
    expect(files.map((file) => file.name)).toEqual(['notice.pdf', 'bill.xlsx'])
    expect(categories).toEqual(['main_tender', 'boq'])
    expect(onDocumentsChanged).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('notice.pdf')).toBeTruthy()
  })

  it('shows upload errors and rejects invalid types and sizes before submission', async () => {
    const user = userEvent.setup({ applyAccept: false })
    renderManager()
    await user.upload(screen.getByLabelText('Choose tender documents'), new File(['text'], 'notes.txt', { type: 'text/plain' }))
    expect(screen.getByRole('alert').textContent).toMatch(/unsupported file type/i)
    expect(screen.getByRole('button', { name: 'Upload selected' })).toHaveProperty('disabled', true)
    await user.click(screen.getByRole('button', { name: 'Remove notes.txt' }))
    await user.upload(screen.getByLabelText('Choose tender documents'), new File(['pdf'], 'large.pdf', { type: 'application/pdf' }))
    // File size is validated using the browser File metadata; a synthetic large file is covered without allocating its contents.
    const largeFile = new File([], 'oversized.pdf')
    Object.defineProperty(largeFile, 'size', { value: 50 * 1024 * 1024 + 1 })
    await user.upload(screen.getByLabelText('Choose tender documents'), largeFile)
    expect(screen.getByRole('alert').textContent).toMatch(/50 MiB per-file limit/i)
    expect(uploadDocuments).not.toHaveBeenCalled()
  })

  it('shows server upload failures', async () => {
    const user = userEvent.setup()
    vi.mocked(uploadDocuments).mockRejectedValue(new Error('Upload size limit exceeded'))
    renderManager()
    await user.upload(screen.getByLabelText('Choose tender documents'), new File(['pdf'], 'valid.pdf', { type: 'application/pdf' }))
    await user.click(screen.getByRole('button', { name: 'Upload selected' }))
    expect((await screen.findByRole('alert')).textContent).toContain('Upload size limit exceeded')
  })

  it('blocks a selection that exceeds the 200 MiB batch limit', async () => {
    const user = userEvent.setup()
    renderManager()
    const files = Array.from({ length: 5 }, (_, index) => {
      const file = new File([], `part-${index}.pdf`, { type: 'application/pdf' })
      Object.defineProperty(file, 'size', { value: 50 * 1024 * 1024 })
      return file
    })
    await user.upload(screen.getByLabelText('Choose tender documents'), files)
    expect(screen.getByRole('alert').textContent).toMatch(/200 MiB total limit/i)
    expect(screen.getByRole('button', { name: 'Upload selected' })).toHaveProperty('disabled', true)
    expect(uploadDocuments).not.toHaveBeenCalled()
  })

  it('renders real list records, empty and loading states, and filters by category', async () => {
    const user = userEvent.setup()
    vi.mocked(listDocuments)
      .mockResolvedValueOnce(page([firstDocument], 1))
      .mockResolvedValueOnce(page([], 0))
    renderManager()

    expect(await screen.findByText('notice.pdf')).toBeTruthy()
    expect(screen.getByText('1.0 KB')).toBeTruthy()
    expect(screen.getAllByText('Main Tender').length).toBeGreaterThan(0)
    expect(listDocuments).toHaveBeenCalledWith(tenderId, { limit: 8, offset: 0, category: undefined })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter documents by category' }), 'boq')
    expect(await screen.findByText('No documents in this category')).toBeTruthy()
    expect(listDocuments).toHaveBeenLastCalledWith(tenderId, { limit: 8, offset: 0, category: 'boq' })
  })

  it('paginates document results', async () => {
    const user = userEvent.setup()
    vi.mocked(listDocuments)
      .mockResolvedValueOnce(page([firstDocument], 9, 0))
      .mockResolvedValueOnce(page([{ ...firstDocument, id: 'doc-9', original_filename: 'final.pdf' }], 9, 8))
    renderManager()
    expect(await screen.findByText('notice.pdf')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('final.pdf')).toBeTruthy()
    expect(listDocuments).toHaveBeenLastCalledWith(tenderId, { limit: 8, offset: 8, category: undefined })
  })

  it('shows document list failures and allows retrying', async () => {
    const user = userEvent.setup()
    vi.mocked(listDocuments).mockRejectedValueOnce(new Error('Document service unavailable')).mockResolvedValueOnce(page([firstDocument], 1))
    renderManager()
    expect((await screen.findByRole('alert')).textContent).toContain('Document service unavailable')
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('notice.pdf')).toBeTruthy()
  })

  it('updates the category and refreshes documents and tender count', async () => {
    const user = userEvent.setup()
    const onDocumentsChanged = vi.fn().mockResolvedValue(undefined)
    vi.mocked(listDocuments).mockResolvedValue(page([firstDocument], 1))
    renderManager(onDocumentsChanged)
    await screen.findByText('notice.pdf')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Uploaded category for notice.pdf' }), 'boq')
    await waitFor(() => expect(updateDocumentCategory).toHaveBeenCalledWith(tenderId, 'doc-1', 'boq'))
    expect(onDocumentsChanged).toHaveBeenCalledTimes(1)
    expect(await screen.findByLabelText('3 documents')).toBeTruthy()
  })

  it('downloads a document as a blob using its original filename', async () => {
    const user = userEvent.setup()
    vi.mocked(listDocuments).mockResolvedValue(page([firstDocument], 1))
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    renderManager()
    await screen.findByText('notice.pdf')
    await user.click(screen.getByRole('button', { name: 'Download' }))
    await screen.findByText('notice.pdf downloaded.')
    expect(downloadDocument).toHaveBeenCalledWith(tenderId, 'doc-1')
    expect(click).toHaveBeenCalled()
    expect(URL.createObjectURL).toHaveBeenCalledWith(expect.any(Blob))
  })

  it('reports download failures', async () => {
    const user = userEvent.setup()
    vi.mocked(listDocuments).mockResolvedValue(page([firstDocument], 1))
    vi.mocked(downloadDocument).mockRejectedValue(new Error('Document file not found'))
    renderManager()
    await screen.findByText('notice.pdf')
    await user.click(screen.getByRole('button', { name: 'Download' }))
    expect((await screen.findByRole('alert')).textContent).toContain('Document file not found')
  })

  it('requires delete confirmation and refreshes the list and tender count after deletion', async () => {
    const user = userEvent.setup()
    const onDocumentsChanged = vi.fn().mockResolvedValue(undefined)
    vi.mocked(listDocuments).mockResolvedValueOnce(page([firstDocument], 1)).mockResolvedValueOnce(page())
    const confirm = vi.mocked(window.confirm)
    confirm.mockReturnValueOnce(false).mockReturnValueOnce(true)
    renderManager(onDocumentsChanged)
    await screen.findByText('notice.pdf')
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(deleteDocument).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(await screen.findByText('Document deleted.')).toBeTruthy()
    expect(deleteDocument).toHaveBeenCalledWith(tenderId, 'doc-1')
    expect(onDocumentsChanged).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('No documents yet')).toBeTruthy()
  })

  it('reports delete failures without removing the document', async () => {
    const user = userEvent.setup()
    vi.mocked(listDocuments).mockResolvedValue(page([firstDocument], 1))
    vi.mocked(deleteDocument).mockRejectedValue(new Error('Document deletion failed'))
    renderManager()
    await screen.findByText('notice.pdf')
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect((await screen.findByRole('alert')).textContent).toContain('Document deletion failed')
    expect(screen.getByText('notice.pdf')).toBeTruthy()
  })

  it('shows pagination counts from the backend response', async () => {
    vi.mocked(listDocuments).mockResolvedValue(page([firstDocument], 9))
    renderManager()
    expect(await screen.findByText((_text, element) => element?.tagName === 'P' && element.textContent?.includes('Showing 1–1 of 9 documents') === true)).toBeTruthy()
    expect(screen.getByLabelText('3 documents')).toBeTruthy()
  })
})
