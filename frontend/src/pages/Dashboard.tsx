import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import Brand from '../components/Brand'
import NewTenderDialog from '../components/NewTenderDialog'
import StatusBadge from '../components/StatusBadge'
import { createMilestone, listMilestones, updateMilestone, type MilestoneCategory, type TenderMilestone } from '../services/milestones'
import {
  createTender,
  getTender,
  listTenders,
  type Tender,
  type TenderCreate,
  type TenderDetail,
  type TenderList,
  type TenderStatus,
} from '../services/tenders'

const PAGE_SIZE = 10
const initialList: TenderList = { items: [], total: 0, limit: PAGE_SIZE, offset: 0 }

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}


function localDateString(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const milestoneCategoryLabels: Record<MilestoneCategory, string> = {
  submission_deadline: 'Submission deadline',
  clarification_deadline: 'Clarification deadline',
  pre_bid_meeting: 'Pre-bid meeting',
  bid_opening: 'Bid opening',
  emd_deadline: 'EMD deadline',
  document_deadline: 'Document deadline',
  other: 'Other milestone',
}

function DashboardCalendar({ tenders, onCreateTender }: { tenders: Tender[]; onCreateTender: () => void }) {
  const [visibleMonth, setVisibleMonth] = useState(() => {
    const now = new Date()
    return new Date(now.getFullYear(), now.getMonth(), 1)
  })
  const [selectedDate, setSelectedDate] = useState(() => new Date())
  const [milestones, setMilestones] = useState<TenderMilestone[]>([])
  const [eventsLoading, setEventsLoading] = useState(false)
  const [eventsError, setEventsError] = useState('')
  const [milestoneDialogOpen, setMilestoneDialogOpen] = useState(false)
  const [milestoneTitle, setMilestoneTitle] = useState('')
  const [milestoneTenderId, setMilestoneTenderId] = useState('')
  const [milestoneCategory, setMilestoneCategory] = useState<MilestoneCategory>('submission_deadline')
  const [milestoneDate, setMilestoneDate] = useState(() => localDateString(new Date()))
  const [milestoneNotes, setMilestoneNotes] = useState('')
  const [milestoneSource, setMilestoneSource] = useState('')
  const [milestoneSaving, setMilestoneSaving] = useState(false)
  const [milestoneActionId, setMilestoneActionId] = useState('')
  const today = new Date()
  const year = visibleMonth.getFullYear()
  const month = visibleMonth.getMonth()
  const firstWeekday = (new Date(year, month, 1).getDay() + 6) % 7
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const cells: Array<number | null> = [
    ...Array.from({ length: firstWeekday }, () => null),
    ...Array.from({ length: daysInMonth }, (_, index) => index + 1),
  ]
  while (cells.length % 7 !== 0) cells.push(null)
  const monthLabel = new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric' }).format(visibleMonth)
  const selectedLabel = new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'short', day: 'numeric' }).format(selectedDate)
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  const monthStart = localDateString(new Date(year, month, 1))
  const monthEnd = localDateString(new Date(year, month + 1, 0))
  const selectedDateKey = localDateString(selectedDate)
  const selectedMilestones = milestones.filter((item) => item.due_date === selectedDateKey)
  const todayKey = localDateString(today)

  const loadMilestones = useCallback(async () => {
    setEventsLoading(true)
    setEventsError('')
    try {
      const result = await listMilestones({ fromDate: monthStart, toDate: monthEnd })
      setMilestones(result.items)
    } catch (reason) {
      setEventsError(reason instanceof Error ? reason.message : 'Calendar milestones could not be loaded.')
    } finally {
      setEventsLoading(false)
    }
  }, [monthStart, monthEnd])

  useEffect(() => { void loadMilestones() }, [loadMilestones])

  async function saveMilestone(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!milestoneTenderId || !milestoneTitle.trim() || !milestoneDate) return
    setMilestoneSaving(true)
    setEventsError('')
    try {
      await createMilestone(milestoneTenderId, {
        title: milestoneTitle.trim(),
        category: milestoneCategory,
        due_date: milestoneDate,
        notes: milestoneNotes.trim() || null,
        source_reference: milestoneSource.trim() || null,
      })
      const newDate = new Date(`${milestoneDate}T12:00:00`)
      setSelectedDate(newDate)
      setVisibleMonth(new Date(newDate.getFullYear(), newDate.getMonth(), 1))
      setMilestoneDialogOpen(false)
      setMilestoneTitle('')
      setMilestoneNotes('')
      setMilestoneSource('')
      await listMilestones({ fromDate: milestoneDate.slice(0, 7) + '-01', toDate: localDateString(new Date(newDate.getFullYear(), newDate.getMonth() + 1, 0)) }).then((result) => setMilestones(result.items))
    } catch (reason) {
      setEventsError(reason instanceof Error ? reason.message : 'The milestone could not be saved.')
    } finally {
      setMilestoneSaving(false)
    }
  }

  async function toggleMilestone(item: TenderMilestone) {
    setMilestoneActionId(item.id)
    setEventsError('')
    try {
      await updateMilestone(item.id, { is_completed: !item.is_completed })
      await loadMilestones()
    } catch (reason) {
      setEventsError(reason instanceof Error ? reason.message : 'The milestone could not be updated.')
    } finally {
      setMilestoneActionId('')
    }
  }

  return (
    <aside className="dashboard-rail">
      <section className="calendar-card" aria-label="Calendar">
        <div className="rail-card-heading">
          <div><p className="eyebrow">PLAN AHEAD</p><h2>Calendar</h2></div>
          <span className="calendar-icon" aria-hidden="true">▦</span>
        </div>
        <div className="calendar-month-nav">
          <button className="calendar-nav-button" aria-label="Previous month" onClick={() => { setVisibleMonth(new Date(year, month - 1, 1)); setSelectedDate(new Date(year, month - 1, 1)) }}>‹</button>
          <strong>{monthLabel}</strong>
          <button className="calendar-nav-button" aria-label="Next month" onClick={() => { setVisibleMonth(new Date(year, month + 1, 1)); setSelectedDate(new Date(year, month + 1, 1)) }}>›</button>
        </div>
        <div className="calendar-grid calendar-weekdays" aria-hidden="true">
          {['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((day, index) => <span key={`${day}-${index}`}>{day}</span>)}
        </div>
        <div className="calendar-grid calendar-days">
          {cells.map((day, index) => {
            if (day === null) return <span className="calendar-day is-empty" key={`empty-${index}`} />
            const date = new Date(year, month, day)
            const isToday = sameDay(date, today)
            const isSelected = sameDay(date, selectedDate)
            return (
              <button
                key={day}
                type="button"
                className={`calendar-day${isToday ? ' is-today' : ''}${isSelected ? ' is-selected' : ''}`}
                aria-label={new Intl.DateTimeFormat(undefined, { dateStyle: 'full' }).format(date)}
                aria-pressed={isSelected}
                onClick={() => setSelectedDate(date)}
              >{day}{milestones.some((item) => item.due_date === localDateString(date) && !item.is_completed) && <span className="calendar-day-event-dot" aria-hidden="true" />}</button>
            )
          })}
        </div>
        <div className="calendar-selection">
          <span className="calendar-selection-dot" />
          <div><strong>{selectedLabel}</strong><span>{selectedMilestones.length ? `${selectedMilestones.length} saved milestone(s)` : eventsLoading ? 'Loading saved milestones…' : 'No saved milestones for this date.'}</span></div>
        </div>
        {eventsError && <p className="calendar-events-error" role="alert">{eventsError}</p>}
        {selectedMilestones.length > 0 && (
          <div className="calendar-event-list" aria-label="Milestones on selected date">
            {selectedMilestones.map((item) => {
              const overdue = !item.is_completed && item.due_date < todayKey
              const dueSoon = !item.is_completed && !overdue && (Date.parse(`${item.due_date}T00:00:00`) - Date.parse(`${todayKey}T00:00:00`)) / 86400000 <= 7
              return (
                <article className={`calendar-event-row${item.is_completed ? ' is-completed' : ''}`} key={item.id}>
                  <div className="calendar-event-copy"><strong>{item.title}</strong><span>{item.tender_name} · {milestoneCategoryLabels[item.category]}</span>{item.source_reference && <small>Source: {item.source_reference}</small>}<em className={overdue ? 'is-overdue' : dueSoon ? 'is-due-soon' : ''}>{item.is_completed ? 'Completed' : overdue ? 'Overdue' : dueSoon ? 'Due soon' : 'Scheduled'}</em></div>
                  <button type="button" className="calendar-event-complete" disabled={milestoneActionId === item.id} onClick={() => void toggleMilestone(item)}>{milestoneActionId === item.id ? 'Saving…' : item.is_completed ? 'Reopen' : 'Complete'}</button>
                </article>
              )
            })}
          </div>
        )}
        <div className="calendar-card-actions">
          <button className="calendar-today-button" onClick={() => { setVisibleMonth(new Date(today.getFullYear(), today.getMonth(), 1)); setSelectedDate(today) }}>Today</button>
          <button type="button" className="calendar-add-button" onClick={() => { setMilestoneDate(selectedDateKey); setMilestoneTenderId(tenders[0]?.id ?? ''); setMilestoneDialogOpen(true) }}>+ Add milestone</button>
        </div>
        {milestoneDialogOpen && (
          <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !milestoneSaving) setMilestoneDialogOpen(false) }}>
            <section className="dialog-card milestone-dialog" role="dialog" aria-modal="true" aria-labelledby="milestone-dialog-title">
              <div className="dialog-heading"><div><p className="eyebrow">TENDER CALENDAR</p><h2 id="milestone-dialog-title">Add a tender milestone</h2></div><button type="button" className="icon-button" aria-label="Close milestone dialog" disabled={milestoneSaving} onClick={() => setMilestoneDialogOpen(false)}>×</button></div>
              <p className="dialog-copy">Add a date confirmed from the tender notice or another trusted source. TenderWise will not invent deadline dates.</p>
              <form className="milestone-form" onSubmit={(event) => void saveMilestone(event)}>
                <label className="field field-full"><span>Milestone title <b>*</b></span><input required maxLength={255} value={milestoneTitle} onChange={(event) => setMilestoneTitle(event.target.value)} placeholder="e.g. Online bid submission closes" /></label>
                <label className="field"><span>Tender workspace <b>*</b></span><select required value={milestoneTenderId} onChange={(event) => setMilestoneTenderId(event.target.value)}><option value="">Select a tender…</option>{tenders.map((tender) => <option key={tender.id} value={tender.id}>{tender.name}{tender.reference_number ? ` · ${tender.reference_number}` : ''}</option>)}</select></label>
                <label className="field"><span>Milestone type</span><select value={milestoneCategory} onChange={(event) => setMilestoneCategory(event.target.value as MilestoneCategory)}>{Object.entries(milestoneCategoryLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
                <label className="field"><span>Date <b>*</b></span><input type="date" required value={milestoneDate} onChange={(event) => setMilestoneDate(event.target.value)} /></label>
                <label className="field"><span>Source reference (optional)</span><input maxLength={500} value={milestoneSource} onChange={(event) => setMilestoneSource(event.target.value)} placeholder="e.g. Tender notice, page 12, clause 4.2" /></label>
                <label className="field field-full"><span>Notes (optional)</span><textarea rows={3} maxLength={5000} value={milestoneNotes} onChange={(event) => setMilestoneNotes(event.target.value)} placeholder="Additional context or action needed…" /></label>
                {tenders.length === 0 && <p className="form-error field-full">No tender workspaces are available. Create a tender before adding a milestone.</p>}
                {eventsError && <p className="form-error field-full" role="alert">{eventsError}</p>}
                <div className="form-actions field-full"><button type="button" className="button button-secondary" disabled={milestoneSaving} onClick={() => setMilestoneDialogOpen(false)}>Cancel</button><button type="submit" className="button button-primary" disabled={milestoneSaving || !tenders.length}>{milestoneSaving ? 'Saving…' : 'Save milestone'}</button></div>
              </form>
            </section>
          </div>
        )}
      </section>
      <section className="quick-actions-card">
        <p className="eyebrow">GET STARTED</p>
        <h2>Move work forward</h2>
        <p>Create a tender workspace to organize documents, analysis and bid preparation.</p>
        <button className="button button-primary quick-create-button" onClick={onCreateTender}><span className="button-plus" aria-hidden="true">+</span> Create tender</button>
      </section>
      <div className="rail-note"><span>✦</span><p><strong>Evidence before assumptions</strong>Keep every decision connected to its source.</p></div>
    </aside>
  )
}

export default function Dashboard({
  onOpenTender,
  onOpenCompanies,
  onCreated,
}: {
  onOpenTender: (id: string) => void
  onOpenCompanies: () => void
  onCreated: (tender: Tender) => Promise<void>
}) {
  const [result, setResult] = useState<TenderList>(initialList)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<TenderStatus | ''>('')
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [dialogOpen, setDialogOpen] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const [milestoneTenderOptions, setMilestoneTenderOptions] = useState<Tender[]>([])
  const [selectedCompareIds, setSelectedCompareIds] = useState<string[]>([])
  const [comparison, setComparison] = useState<TenderDetail[] | null>(null)
  const [compareBusy, setCompareBusy] = useState(false)
  const [compareError, setCompareError] = useState('')
  const tendersSectionRef = useRef<HTMLElement | null>(null)

  const load = useCallback(async (active: () => boolean = () => true) => {
    setLoading(true)
    setError('')
    try {
      const data = await listTenders({
        limit: PAGE_SIZE,
        offset,
        search,
        status: status || undefined,
      })
      if (active()) setResult(data)
    } catch (reason) {
      if (active()) setError(reason instanceof Error ? reason.message : 'Tenders could not be loaded.')
    } finally {
      if (active()) setLoading(false)
    }
  }, [offset, search, status])

  useEffect(() => {
    let current = true
    const timer = window.setTimeout(() => void load(() => current), search.trim() ? 250 : 0)
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [load, refreshKey, search])

  useEffect(() => {
    let active = true
    listTenders({ limit: 100, offset: 0 })
      .then((data) => { if (active) setMilestoneTenderOptions(data.items) })
      .catch(() => { if (active) setMilestoneTenderOptions([]) })
    return () => { active = false }
  }, [refreshKey])

  async function handleCreate(payload: TenderCreate): Promise<Tender> {
    const tender = await createTender(payload)
    setDialogOpen(false)
    await load()
    await onCreated(tender)
    return tender
  }

  async function handleCompareSelected() {
    if (selectedCompareIds.length < 2 || selectedCompareIds.length > 3) return
    setCompareBusy(true)
    setCompareError('')
    try {
      const details = await Promise.all(selectedCompareIds.map((id) => getTender(id)))
      setComparison(details)
    } catch (reason) {
      setCompareError(reason instanceof Error ? reason.message : 'The selected tenders could not be compared.')
    } finally {
      setCompareBusy(false)
    }
  }

  function toggleCompareTender(id: string, checked: boolean) {
    setSelectedCompareIds((current) => {
      if (checked) return current.length >= 3 ? current : [...current, id]
      return current.filter((value) => value !== id)
    })
  }

  const totalPages = Math.max(1, Math.ceil(result.total / PAGE_SIZE))
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1
  const firstItem = result.total === 0 ? 0 : offset + 1
  const lastItem = Math.min(offset + PAGE_SIZE, result.total)
  const activeOnPage = result.items.filter((item) => item.status === 'active').length
  const draftOnPage = result.items.filter((item) => item.status === 'draft').length
  const organizationsOnPage = new Set(result.items.map((item) => item.issuing_organization).filter(Boolean)).size

  return (
    <div className="page-wrap dashboard-shell">
      <aside className="app-sidebar" aria-label="Main navigation">
        <div className="sidebar-brand"><Brand /><span className="sidebar-workspace-label">PROCUREMENT INTELLIGENCE</span></div>
        <div className="sidebar-section-label">WORKSPACE</div>
        <nav className="sidebar-nav">
          <a className="sidebar-nav-item is-active" href="/" aria-current="page"><span className="nav-symbol">⌂</span>Dashboard</a>
          <button className="sidebar-nav-item" type="button" aria-controls="tenders" onClick={() => { setSearch(''); setStatus(''); setOffset(0); setSelectedCompareIds([]); tendersSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}><span className="nav-symbol">▤</span>Tenders</button>
          <button className="sidebar-nav-item" onClick={onOpenCompanies}><span className="nav-symbol">▣</span>Companies</button>
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-help-mark">i</div>
          <div><strong>Evidence-first decisions</strong><span>Every finding should lead back to its source.</span></div>
        </div>
      </aside>

      <div className="dashboard-content">
        <header className="dashboard-topbar">
          <div className="topbar-breadcrumb"><span>Workspace</span><span>/</span><strong>Dashboard</strong></div>
          <div className="topbar-right"><span className="topbar-status-dot" /> Tender workspace</div>
        </header>

        <main className="dashboard-main">
          <section className="page-heading">
            <div>
              <p className="eyebrow">PROCUREMENT OVERVIEW</p>
              <h1>Good work starts with clarity.</h1>
              <p className="page-subtitle">Manage tender workspaces, review progress and keep bid preparation moving.</p>
            </div>
            <div className="dashboard-heading-actions">
              <button className="button button-secondary" onClick={onOpenCompanies}><span aria-hidden="true">▣</span> Company documents</button>
              <button className="button button-primary" onClick={() => setDialogOpen(true)}>
                <span className="button-plus" aria-hidden="true">+</span> New tender
              </button>
            </div>
          </section>

          <section className="dashboard-metrics" aria-label="Tender overview metrics">
            <article className="dashboard-metric metric-primary">
              <div className="metric-topline"><span>Tenders in results</span><span className="metric-icon">▤</span></div>
              <strong>{loading ? '—' : result.total}</strong>
              <p>Matching the current search and filters</p>
            </article>
            <article className="dashboard-metric">
              <div className="metric-topline"><span>Active on this page</span><span className="metric-icon metric-icon-teal">●</span></div>
              <strong>{loading ? '—' : activeOnPage}</strong>
              <p>Currently marked active</p>
            </article>
            <article className="dashboard-metric">
              <div className="metric-topline"><span>Drafts on this page</span><span className="metric-icon metric-icon-amber">✎</span></div>
              <strong>{loading ? '—' : draftOnPage}</strong>
              <p>Workspaces in preparation</p>
            </article>
            <article className="dashboard-metric">
              <div className="metric-topline"><span>Organizations shown</span><span className="metric-icon metric-icon-violet">⌂</span></div>
              <strong>{loading ? '—' : organizationsOnPage}</strong>
              <p>Distinct issuers on this page</p>
            </article>
          </section>

          <div className="dashboard-body-grid">
            <section className="dashboard-work-section" id="tenders" ref={tendersSectionRef}>
            <div className="dashboard-section-heading">
              <div><p className="eyebrow">YOUR PIPELINE</p><h2>Tender workspaces</h2><p>Search and open a workspace to continue your bid preparation.</p></div>
              <div className="tender-section-actions">
                <span className="results-count">{result.total} matches</span>
                <button type="button" className="button button-secondary button-small" disabled={selectedCompareIds.length < 2 || compareBusy} onClick={() => void handleCompareSelected()}>{compareBusy ? 'Comparing…' : `Compare (${selectedCompareIds.length}/3)`}</button>
              </div>
            </div>
            {compareError && <p className="form-error compare-inline-error" role="alert">{compareError}</p>}
            <section className="workspace-panel" aria-label="Tender workspaces">
              <div className="list-toolbar">
                <label className="search-box">
                  <span className="search-icon" aria-hidden="true">⌕</span>
                  <input
                    type="search"
                    value={search}
                    onChange={(event) => { setSearch(event.target.value); setOffset(0); setSelectedCompareIds([]) }}
                    placeholder="Search by tender name, reference or organization"
                    aria-label="Search tenders"
                  />
                </label>
                <label className="filter-control">
                  <span>Status</span>
                  <select
                    aria-label="Filter by status"
                    value={status}
                    onChange={(event) => { setStatus(event.target.value as TenderStatus | ''); setOffset(0); setSelectedCompareIds([]) }}
                  >
                    <option value="">All statuses</option>
                    <option value="draft">Draft</option>
                    <option value="active">Active</option>
                    <option value="archived">Archived</option>
                  </select>
                </label>
              </div>

              {loading ? (
                <div className="loading-state" role="status"><span className="spinner" /> Loading tender workspaces…</div>
              ) : error ? (
                <div className="dashboard-error" role="alert">
                  <h2>We couldn’t load your tenders</h2>
                  <p>{error}</p>
                  <button className="button button-secondary" onClick={() => setRefreshKey((value) => value + 1)}>Try again</button>
                </div>
              ) : result.total === 0 ? (
                <div className="empty-state">
                  <div className="empty-illustration" aria-hidden="true">T</div>
                  <h2>{search || status ? 'No matching tenders' : 'Start with your first tender'}</h2>
                  <p>{search || status ? 'Try another search or status filter.' : 'Create an empty workspace, then add tender details and documents as you go.'}</p>
                  {!search && !status && <button className="button button-primary" onClick={() => setDialogOpen(true)}>Create your first tender</button>}
                </div>
              ) : (
                <>
                  <div className="table-scroll">
                    <table className="tender-table">
                      <thead>
                        <tr>
                          <th scope="col"><span className="visually-hidden">Select for comparison</span></th>
                          <th scope="col">Tender</th>
                          <th scope="col">Reference</th>
                          <th scope="col">Issuing organization</th>
                          <th scope="col">Status</th>
                          <th scope="col">Last updated</th>
                          <th scope="col"><span className="visually-hidden">Actions</span></th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.items.map((tender) => (
                          <tr key={tender.id}>
                            <td data-label="Compare"><input className="tender-compare-checkbox" type="checkbox" aria-label={`Select ${tender.name} for comparison`} checked={selectedCompareIds.includes(tender.id)} disabled={!selectedCompareIds.includes(tender.id) && selectedCompareIds.length >= 3} onChange={(event) => toggleCompareTender(tender.id, event.target.checked)} /></td>
                            <td data-label="Tender"><span className="tender-name">{tender.name}</span></td>
                            <td data-label="Reference" className="muted-cell">{tender.reference_number || '—'}</td>
                            <td data-label="Issuing organization" className="muted-cell">{tender.issuing_organization || '—'}</td>
                            <td data-label="Status"><StatusBadge status={tender.status} /></td>
                            <td data-label="Last updated" className="muted-cell">{formatDate(tender.updated_at)}</td>
                            <td className="action-cell">
                              <button className="open-link" onClick={() => onOpenTender(tender.id)}>
                                Open workspace <span aria-hidden="true">→</span>
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="pagination">
                    <p>Showing <strong>{firstItem}–{lastItem}</strong> of <strong>{result.total}</strong> tenders</p>
                    <div className="pagination-controls">
                      <button className="button button-secondary button-small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
                      <span>Page {currentPage} of {totalPages}</span>
                      <button className="button button-secondary button-small" disabled={offset + PAGE_SIZE >= result.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
                    </div>
                  </div>
                </>
              )}
            </section>
            </section>
            <DashboardCalendar tenders={milestoneTenderOptions} onCreateTender={() => setDialogOpen(true)} />
          </div>
          <p className="page-footnote">Calendar milestones are linked to tender workspaces and saved only from dates you confirm against trusted sources.</p>
        </main>
      </div>
      {comparison && (
        <div className="dialog-backdrop compare-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setComparison(null) }}>
          <section className="dialog-card dialog-wide compare-dialog" role="dialog" aria-modal="true" aria-labelledby="compare-tenders-title">
            <div className="dialog-heading">
              <div><p className="eyebrow">TENDER WORKSPACE COMPARISON</p><h2 id="compare-tenders-title">Compare selected tenders</h2><p className="dialog-copy">A side-by-side comparison of saved workspace information. This view does not infer technical, financial or eligibility equivalence.</p></div>
              <button type="button" className="icon-button" aria-label="Close comparison" onClick={() => setComparison(null)}>×</button>
            </div>
            <div className="comparison-table-wrap">
              <table className="comparison-table">
                <thead><tr><th scope="col">Workspace field</th>{comparison.map((item) => <th scope="col" key={item.id}><strong>{item.name}</strong><span>{item.reference_number || 'No reference'}</span></th>)}</tr></thead>
                <tbody>
                  {([
                    ['Issuing organization', (item: TenderDetail) => item.issuing_organization || 'Not provided'],
                    ['Department', (item: TenderDetail) => item.department || 'Not provided'],
                    ['Status', (item: TenderDetail) => item.status.replace('_', ' ')],
                    ['Uploaded documents', (item: TenderDetail) => String(item.document_count)],
                    ['Created', (item: TenderDetail) => formatDate(item.created_at)],
                    ['Last updated', (item: TenderDetail) => formatDate(item.updated_at)],
                  ] as Array<[string, (item: TenderDetail) => string]>).map(([label, value]) => (
                    <tr key={label}><th scope="row">{label}</th>{comparison.map((item) => <td key={item.id}>{value(item)}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="comparison-note"><strong>What this comparison means:</strong> Values are read directly from TenderWise workspace records. To compare EMD, deadlines, scope or qualification criteria, run the analysis agents for each tender and verify the original source clauses.</div>
            <div className="form-actions"><button type="button" className="button button-secondary" onClick={() => setComparison(null)}>Close</button></div>
          </section>
        </div>
      )}
      {dialogOpen && <NewTenderDialog onClose={() => setDialogOpen(false)} onCreate={handleCreate} />}
    </div>
  )
}
