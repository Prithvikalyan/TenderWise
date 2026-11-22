import { useCallback, useEffect, useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import {
  deleteDocument,
  DOCUMENT_CATEGORIES,
  downloadDocument,
  listDocuments,
  updateDocumentCategory,
  uploadDocuments,
  type DocumentCategory,
  type TenderDocument,
} from '../services/documents'

const PAGE_SIZE = 8
const MAX_FILE_SIZE = 50 * 1024 * 1024
const MAX_BATCH_SIZE = 200 * 1024 * 1024
const ALLOWED_EXTENSIONS = new Set(['pdf', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'png', 'jpg', 'jpeg'])

interface SelectedDocument {
  key: number
  file: File
  category: DocumentCategory
}

function categoryLabel(category: DocumentCategory): string {
  return category.replace(/_/g, ' ').replace(/\b\w/g, (letter: string) => letter.toUpperCase())
}

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MiB`
}

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

function fileProblem(file: File): string {
  const extension = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (!ALLOWED_EXTENSIONS.has(extension)) return 'Unsupported file type. Choose PDF, DOC, DOCX, XLS, XLSX, CSV, PNG, JPG or JPEG.'
  if (file.size === 0) return 'Empty files cannot be uploaded.'
  if (file.size > MAX_FILE_SIZE) return 'This file exceeds the 50 MiB per-file limit.'
  return ''
}

function categorySelectLabel(category: DocumentCategory): string {
  return categoryLabel(category)
}

function triggerDownload(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(objectUrl)
}

export default function DocumentManager({
  tenderId,
  documentCount,
  onDocumentsChanged,
}: {
  tenderId: string
  documentCount: number
  onDocumentsChanged: () => Promise<void>
}) {
  const [documents, setDocuments] = useState<TenderDocument[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [filter, setFilter] = useState<DocumentCategory | ''>('')
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState('')
  const [selected, setSelected] = useState<SelectedDocument[]>([])
  const [feedback, setFeedback] = useState('')
  const [failure, setFailure] = useState('')
  const [uploading, setUploading] = useState(false)
  const [busyDocument, setBusyDocument] = useState<string | null>(null)
  const nextKey = useRef(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const loadDocuments = useCallback(async (active: () => boolean = () => true) => {
    setLoading(true)
    setListError('')
    try {
      const page = await listDocuments(tenderId, {
        limit: PAGE_SIZE,
        offset,
        category: filter || undefined,
      })
      if (active()) {
        setDocuments(page.items)
        setTotal(page.total)
      }
    } catch (reason) {
      if (active()) setListError(reason instanceof Error ? reason.message : 'Documents could not be loaded.')
    } finally {
      if (active()) setLoading(false)
    }
  }, [filter, offset, tenderId])

  useEffect(() => {
    let current = true
    void loadDocuments(() => current)
    return () => { current = false }
  }, [loadDocuments])

  const addFiles = (files: FileList | File[]) => {
    setFeedback('')
    setFailure('')
    const additions = Array.from(files).map((file) => ({
      key: nextKey.current++,
      file,
      category: 'main_tender' as DocumentCategory,
    }))
    setSelected((current) => [...current, ...additions])
  }

  const batchProblem = selected.some(({ file }) => fileProblem(file))
    ? ''
    : selected.reduce((sum, item) => sum + item.file.size, 0) > MAX_BATCH_SIZE
      ? 'The selected batch exceeds the 200 MiB total limit. Remove files to continue.'
      : ''
  const selectedProblem = selected.find(({ file }) => fileProblem(file))
  const validationMessage = selectedProblem
    ? `${selectedProblem.file.name}: ${fileProblem(selectedProblem.file)}`
    : batchProblem

  const handleFileInput = (event: ChangeEvent<HTMLInputElement>) => {
    if (event.target.files) addFiles(event.target.files)
    event.target.value = ''
  }

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    if (event.dataTransfer.files.length) addFiles(event.dataTransfer.files)
  }

  async function refreshAfterChange() {
    await loadDocuments()
    await onDocumentsChanged()
  }

  async function handleUpload() {
    if (!selected.length || validationMessage) return
    setUploading(true)
    setFailure('')
    setFeedback('')
    try {
      await uploadDocuments(tenderId, selected.map((item) => item.file), selected.map((item) => item.category))
      setSelected([])
      setOffset(0)
      setFeedback('Documents uploaded successfully.')
      if (offset !== 0) setOffset(0)
      else await loadDocuments()
      await onDocumentsChanged()
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : 'The documents could not be uploaded.')
    } finally {
      setUploading(false)
    }
  }

  async function handleCategoryChange(document: TenderDocument, category: DocumentCategory) {
    setBusyDocument(document.id)
    setFailure('')
    setFeedback('')
    try {
      await updateDocumentCategory(tenderId, document.id, category)
      setFeedback('Document category updated.')
      await refreshAfterChange()
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : 'The document category could not be updated.')
    } finally {
      setBusyDocument(null)
    }
  }

  async function handleDownload(document: TenderDocument) {
    setBusyDocument(document.id)
    setFailure('')
    setFeedback('')
    try {
      const blob = await downloadDocument(tenderId, document.id)
      triggerDownload(blob, document.original_filename)
      setFeedback(`${document.original_filename} downloaded.`)
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : 'The document could not be downloaded.')
    } finally {
      setBusyDocument(null)
    }
  }

  async function handleDelete(document: TenderDocument) {
    if (!window.confirm(`Delete “${document.original_filename}”? This cannot be undone.`)) return
    setBusyDocument(document.id)
    setFailure('')
    setFeedback('')
    try {
      await deleteDocument(tenderId, document.id)
      setFeedback('Document deleted.')
      const nextOffset = documents.length === 1 && offset > 0 ? offset - PAGE_SIZE : offset
      if (nextOffset !== offset) setOffset(nextOffset)
      else await loadDocuments()
      await onDocumentsChanged()
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : 'The document could not be deleted.')
    } finally {
      setBusyDocument(null)
    }
  }

  const page = Math.floor(offset / PAGE_SIZE) + 1
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const batchSize = selected.reduce((sum, item) => sum + item.file.size, 0)

  return (
    <section className="content-card document-manager" aria-labelledby="documents-heading">
      <div className="section-heading">
        <div><p className="eyebrow">Tender package</p><h2 id="documents-heading">Documents</h2></div>
        <span className="count-badge" aria-label={`${documentCount} documents`}>{documentCount}</span>
      </div>

      <div
        className="document-dropzone"
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop}
        aria-label="Document upload area"
      >
        <input
          ref={inputRef}
          className="visually-hidden"
          type="file"
          multiple
          accept=".pdf,.doc,.docx,.xls,.xlsx,.csv,.png,.jpg,.jpeg"
          aria-label="Choose tender documents"
          onChange={handleFileInput}
        />
        <span className="document-icon" aria-hidden="true">⇧</span>
        <div>
          <p><strong>Drop files here</strong> or <button type="button" className="text-button" onClick={() => inputRef.current?.click()}>browse</button></p>
          <span>PDF, DOC, DOCX, XLS, XLSX, CSV, PNG, JPG and JPEG · 50 MiB per file · 200 MiB per batch</span>
        </div>
      </div>

      {selected.length > 0 && (
        <div className="selected-documents" aria-label="Selected files">
          <div className="selected-documents-heading">
            <strong>{selected.length} selected</strong>
            <span>{formatSize(batchSize)} total</span>
          </div>
          <ul>
            {selected.map((item) => {
              const problem = fileProblem(item.file)
              return (
                <li key={item.key} className="selected-document-row">
                  <div className="selected-document-name">
                    <strong title={item.file.name}>{item.file.name}</strong>
                    <span>{formatSize(item.file.size)}</span>
                    {problem && <span className="form-error">{problem}</span>}
                  </div>
                  <label className="document-category-field">
                    <span className="visually-hidden">Category for {item.file.name}</span>
                    <select
                      aria-label={`Category for ${item.file.name}`}
                      value={item.category}
                      onChange={(event) => setSelected((current) => current.map((selectedFile) => selectedFile.key === item.key
                        ? { ...selectedFile, category: event.target.value as DocumentCategory }
                        : selectedFile))}
                    >
                      {DOCUMENT_CATEGORIES.map((category) => <option key={category} value={category}>{categorySelectLabel(category)}</option>)}
                    </select>
                  </label>
                  <button
                    type="button"
                    className="icon-button remove-selected"
                    aria-label={`Remove ${item.file.name}`}
                    onClick={() => setSelected((current) => current.filter(({ key }) => key !== item.key))}
                  >×</button>
                </li>
              )
            })}
          </ul>
          {validationMessage && <p className="form-error document-validation" role="alert">{validationMessage}</p>}
          <div className="document-upload-actions">
            <button type="button" className="button button-secondary button-small" onClick={() => setSelected([])} disabled={uploading}>Clear selection</button>
            <button type="button" className="button button-primary button-small" onClick={() => void handleUpload()} disabled={uploading || Boolean(validationMessage)}>
              {uploading ? 'Uploading documents…' : 'Upload selected'}
            </button>
          </div>
        </div>
      )}

      {feedback && <p className="document-feedback" role="status">{feedback}</p>}
      {failure && <p className="form-error document-feedback" role="alert">{failure}</p>}

      <div className="document-list-toolbar">
        <h3>Uploaded documents</h3>
        <label className="document-filter">
          <span>Category</span>
          <select
            aria-label="Filter documents by category"
            value={filter}
            onChange={(event) => { setFilter(event.target.value as DocumentCategory | ''); setOffset(0) }}
          >
            <option value="">All categories</option>
            {DOCUMENT_CATEGORIES.map((category) => <option key={category} value={category}>{categorySelectLabel(category)}</option>)}
          </select>
        </label>
      </div>

      {loading ? (
        <div className="loading-state document-loading" role="status"><span className="spinner" /> Loading documents…</div>
      ) : listError ? (
        <div className="document-list-error" role="alert">
          <span>{listError}</span>
          <button type="button" className="text-button" onClick={() => void loadDocuments()}>Try again</button>
        </div>
      ) : documents.length === 0 ? (
        <div className="documents-empty document-empty-state">
          <div className="document-icon" aria-hidden="true">▤</div>
          <h3>{filter ? 'No documents in this category' : 'No documents yet'}</h3>
          <p>{filter ? 'Choose another category or upload a document.' : 'Upload tender files to keep this package organized.'}</p>
        </div>
      ) : (
        <>
          <div className="document-table-wrap">
            <table className="document-table">
              <thead><tr><th>File</th><th>Category</th><th>Size</th><th>Uploaded</th><th><span className="visually-hidden">Actions</span></th></tr></thead>
              <tbody>
                {documents.map((document) => (
                  <tr key={document.id}>
                    <td data-label="File"><strong className="document-filename" title={document.original_filename}>{document.original_filename}</strong></td>
                    <td data-label="Category">
                      <label className="document-category-field">
                        <span className="visually-hidden">Category for {document.original_filename}</span>
                        <select
                          aria-label={`Uploaded category for ${document.original_filename}`}
                          value={document.category}
                          disabled={busyDocument === document.id}
                          onChange={(event) => void handleCategoryChange(document, event.target.value as DocumentCategory)}
                        >
                          {DOCUMENT_CATEGORIES.map((category) => <option key={category} value={category}>{categorySelectLabel(category)}</option>)}
                        </select>
                      </label>
                    </td>
                    <td data-label="Size" className="muted-cell">{formatSize(document.size_bytes)}</td>
                    <td data-label="Uploaded" className="muted-cell">{formatDate(document.created_at)}</td>
                    <td data-label="Actions" className="document-actions-cell">
                      <button type="button" className="text-button" disabled={busyDocument === document.id} onClick={() => void handleDownload(document)}>Download</button>
                      <button type="button" className="text-button document-delete-action" disabled={busyDocument === document.id} onClick={() => void handleDelete(document)}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {total > PAGE_SIZE && (
            <div className="pagination document-pagination">
              <p>Showing <strong>{offset + 1}–{Math.min(offset + documents.length, total)}</strong> of <strong>{total}</strong> documents</p>
              <div className="pagination-controls">
                <button type="button" className="button button-secondary button-small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
                <span>Page {page} of {pages}</span>
                <button type="button" className="button button-secondary button-small" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  )
}
