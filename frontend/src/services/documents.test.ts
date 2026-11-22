import { afterEach, describe, expect, it, vi } from 'vitest'
import { uploadDocuments } from './documents'

afterEach(() => vi.unstubAllGlobals())

describe('document API upload contract', () => {
  it('sends repeated files and categories in matching order without setting multipart Content-Type', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => [],
    })
    vi.stubGlobal('fetch', fetchMock)
    const files = [
      new File(['notice'], 'notice.pdf', { type: 'application/pdf' }),
      new File(['bill'], 'bill.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }),
    ]

    await uploadDocuments('tender/id', files, ['main_tender', 'boq'])

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, request] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/tenders/tender%2Fid/documents')
    expect(request.method).toBe('POST')
    expect(request.headers).not.toHaveProperty('Content-Type')
    expect(request.body).toBeInstanceOf(FormData)
    const form = request.body as FormData
    expect((form.getAll('files') as File[]).map((file) => file.name)).toEqual(['notice.pdf', 'bill.xlsx'])
    expect(form.getAll('categories')).toEqual(['main_tender', 'boq'])
  })
})
