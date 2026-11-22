# Company Evidence Matching UX update

## Changes

- Kept the Evidence Matching panel mounted while other tender workspace tabs are selected, so its in-memory state is not discarded during tab navigation.
- Remembered the selected company profile per tender in browser local storage.
- Loaded saved evidence-matching run history from the existing backend list/detail APIs when a company is selected, restored the latest saved assessment, and added a saved-assessment selector.
- Updated the Bid Readiness saved-run labels to show human-readable assessment dates instead of truncated run UUIDs. Removed the truncated run UUID from the linked-context pill and the checklist heading.
- Reworked the evidence report into a compact summary-first layout. Requirement cards and coverage statistics are expandable.
- Replaced numeric text relevance percentages with qualitative labels (high/moderate/low text relevance); this remains a retrieval signal, not proof of compliance.
- Removed extraction-unit UUIDs, version UUIDs, and tender source IDs from the visible evidence details. Extraction source labels now use human-readable types and page/paragraph/table locations.
- Updated Evidence Matching component tests for the revised relevance and location labels and the saved-run API mocks.

## Persistence behavior

The saved assessment itself remains in the existing backend database. Browser local storage only remembers the selected company profile for a tender; it does not store report content. On selection or refresh, the panel retrieves the latest saved run from the backend. Switching tabs does not unmount the panel.

## Validation limits

The uploaded archive was inspected and its ZIP integrity was valid. Frontend dependency installation did not complete in this environment (npm install timed out), so the frontend typecheck, Vitest suite, and production build could not be run here. No backend code or database migration was changed.
