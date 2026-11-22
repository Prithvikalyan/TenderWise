import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMilestone, listMilestones, updateMilestone } from './milestones'

const sample = {
  id: 'milestone-1',
  tender_id: 'tender-1',
  tender_name: 'Bridge works',
  tender_reference: 'BR-2026-01',
  title: 'Bid submission closes',
  category: 'submission_deadline' as const,
  due_date: '2026-12-15',
  notes: null,
  source_reference: 'Tender notice, page 8',
  is_completed: false,
  created_at: '2026-11-22T10:00:00Z',
  updated_at: '2026-11-22T10:00:00Z',
}

afterEach(() => vi.restoreAllMocks())

describe('tender milestone API client', () => {
  it('lists milestones for an explicit calendar range', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ items: [sample], total: 1 }), { status: 200 }))
    const result = await listMilestones({ fromDate: '2026-12-01', toDate: '2026-12-31', includeCompleted: false })

    expect(fetchMock).toHaveBeenCalledWith('/api/milestones?from_date=2026-12-01&to_date=2026-12-31&include_completed=false', expect.any(Object))
    expect(result.items[0].id).toBe(sample.id)
  })

  it('creates a source-referenced milestone for the selected tender', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(sample), { status: 201 }))
    await createMilestone('tender-1', {
      title: sample.title,
      category: sample.category,
      due_date: sample.due_date,
      source_reference: sample.source_reference,
    })

    expect(fetchMock).toHaveBeenCalledWith('/api/tenders/tender-1/milestones', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ title: sample.title, category: sample.category, due_date: sample.due_date, source_reference: sample.source_reference }),
    }))
  })

  it('persists completion status', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ...sample, is_completed: true }), { status: 200 }))
    const result = await updateMilestone('milestone-1', { is_completed: true })

    expect(fetchMock).toHaveBeenCalledWith('/api/milestones/milestone-1', expect.objectContaining({
      method: 'PATCH',
      body: JSON.stringify({ is_completed: true }),
    }))
    expect(result.is_completed).toBe(true)
  })
})
