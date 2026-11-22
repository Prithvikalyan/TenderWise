# TenderWise Advanced RAG — Implementation Report

**Baseline:** `TenderWise-integrated-upgrade.zip`  
**Implementation date:** 2026-11-22  
**Status:** Code implementation and offline validation complete; live PostgreSQL/pgvector and hosted NVIDIA validation remain environment-dependent.

## Executive summary

The six requested phases have been implemented in the project source. Existing tender APIs, document processing, agent workflows, evidence matching, readiness reporting and verification workflows were retained.

The current retrieval path is now a tender-scoped hybrid RAG pipeline:

1. Structure-aware retrieval chunks are built from successful extraction units.
2. BM25 searches lexical terms independently.
3. Dense retrieval searches the PostgreSQL pgvector embedding store independently.
4. Rule-based topic query variants provide additional semantic query candidates.
5. Reciprocal Rank Fusion (RRF) merges the independent rankings.
6. An optional NVIDIA cross-encoder reranks the fused candidates.
7. Evidence passages retain document/page/worksheet/table/unit references.
8. Citation IDs are validated and factual claims receive a conservative lexical-overlap and numeric-value check.

The claim check is a review heuristic, not an entailment model. A “support overlap found” result is not proof that a claim is legally or factually correct, and it must not be treated as a compliance decision.

## Phase-by-phase implementation

### Phase 1 — Enable and inspect existing hybrid RAG

- Semantic retrieval is enabled by default in application settings, but it only runs when `NVIDIA_API_KEY` is configured and the database is PostgreSQL with pgvector installed.
- Keyword-only fallback remains available when semantic retrieval is disabled, unconfigured or unavailable.
- Q&A now reports retrieval mode, candidate counts, embedding coverage, RRF/reranker mode and timing trace.
- A retrieval status endpoint reports PostgreSQL/pgvector availability and active retrieval configuration.
- A per-tender telemetry endpoint summarizes the last 30 days. Telemetry stores a SHA-256 question hash and operational metrics, not the raw question or answer.

### Phase 2 — Structure-aware chunking

- Added a versioned chunker that builds retrieval passages from document extraction units.
- Text grouping retains section-heading context and source location metadata.
- Tables are chunked by rows and each table chunk repeats its column headings.
- Chunks preserve extraction-unit IDs, document ID, processing-run-item ID, source references, token count, content hash and chunker version.
- Newly completed tender document-processing items create retrieval chunks transactionally with extraction units.
- Previously processed tenders are supported through lazy chunk backfill during retrieval or the explicit backfill command.
- Retrieval selects only the latest successfully completed processing item per tender document, avoiding older successful runs being mixed with the latest successful run.

### Phase 3 — PostgreSQL + pgvector

- Added a fixed-dimension SQLAlchemy vector adapter; PostgreSQL stores native `VECTOR(2048)`. SQLite uses JSON only for unit tests.
- Added model-versioned chunk embedding cache keyed by chunk, embedding model and content hash.
- Added an additive migration `0011_advanced_rag_storage`.
- The migration creates the pgvector extension, retrieval chunk/embedding tables, chat conversation/message tables and retrieval telemetry.
- Added an HNSW expression index using `embedding::halfvec(2048)`, because the configured NVIDIA embedding model returns 2,048 dimensions and pgvector's indexed `vector` type has a 2,000-dimensional HNSW limit. The query uses a configurable, transaction-local `hnsw.ef_search` setting (default 100).
- The migration does not delete old extraction units or the pre-existing JSON embedding cache. Old JSON embeddings are not silently cast into new chunk embeddings; use the new backfill command to create chunk-level vectors.

### Phase 4 — Advanced retrieval

- Independent BM25 and dense candidate lists are retrieved, rather than restricting semantic search to the BM25 shortlist.
- Deterministic topic classification produces focused rule-based subqueries for supported tender topics.
- RRF combines lexical and dense rankings.
- NVIDIA Nemotron 3.5 Rerank 8B is integrated as an optional cross-encoder reranker. It is disabled by default; provider failures fall back to RRF order.
- The retrieval trace records lexical/dense/fused candidate counts and reranker state.
- SQL retrieval scanning is bounded at the query layer.

### Phase 5 — Q&A intelligence

- Added persistent tender-specific conversations and messages.
- Follow-up messages use recent conversation turns to resolve the user's intent; the tender document retrieval still provides the evidence.
- Both non-streaming and SSE streaming Q&A persist the final answer and source payload.
- Saved conversation list/detail/delete endpoints are available.
- Citation IDs are filtered against the source IDs actually supplied to the model.
- Factual sentences are flagged for review when they have no valid citation, poor lexical overlap with cited text, or numeric values that do not match complete numeric tokens in the cited passage.
- The Q&A frontend can select saved conversations, start a new conversation, delete a conversation, and show retrieval diagnostics.

### Phase 6 — Evaluation and monitoring

- Added Recall@k, Precision@k, MRR and nDCG@k ranking metrics.
- Added a JSONL evaluation runner requiring human-labelled tender IDs and source locations.
- Added a dataset example file as a schema template.
- Added per-tender retrieval telemetry and global configuration status endpoint.
- No real benchmark result is claimed: the example dataset is not a gold dataset. Populate it with manually verified sources from representative tender documents before interpreting retrieval-quality scores.

## Exact changed files relative to the supplied integrated-upgrade ZIP

### New files (14)

- `backend/app/db/vector_types.py`
- `backend/app/evaluation/__init__.py`
- `backend/app/evaluation/golden_dataset.example.jsonl`
- `backend/app/evaluation/metrics.py`
- `backend/app/evaluation/rag_evaluate.py`
- `backend/app/models/retrieval.py`
- `backend/app/services/advanced_retrieval.py`
- `backend/app/services/retrieval_backfill.py`
- `backend/app/services/retrieval_chunks.py`
- `backend/migrations/versions/0011_advanced_rag_storage.py`
- `backend/tests/test_advanced_rag_components.py`
- `backend/tests/test_qa_conversations.py`
- `backend/tests/test_reranker.py`
- `ADVANCED_RAG_IMPLEMENTATION_REPORT.md`

### Modified files (15)

- `.env.example`
- `README.md`
- `backend/app/api/routes/qa.py`
- `backend/app/core/config.py`
- `backend/app/main.py`
- `backend/app/models/__init__.py`
- `backend/app/schemas/qa.py`
- `backend/app/services/ai.py`
- `backend/app/services/processing_worker.py`
- `backend/app/services/qa.py`
- `backend/migrations/env.py`
- `backend/tests/test_qa_retrieval.py`
- `frontend/src/components/IntelligencePanel.tsx`
- `frontend/src/services/intelligence.ts`
- `frontend/src/style.css`

No files were removed. The existing API routes and non-RAG agent services were preserved.

## Configuration

Copy `.env.example` to `.env`, then set the real PostgreSQL URL and NVIDIA API key. Do not commit `.env`.

Important settings:

```dotenv
TENDERWISE_SEMANTIC_RETRIEVAL_ENABLED=true
TENDERWISE_RERANKER_ENABLED=false
TENDERWISE_CHUNK_TARGET_TOKENS=600
TENDERWISE_CHUNK_OVERLAP_TOKENS=80
TENDERWISE_QA_CANDIDATE_K=50
TENDERWISE_QA_RRF_K=60
TENDERWISE_QA_HNSW_EF_SEARCH=100
TENDERWISE_QA_MAX_VECTOR_BACKFILL_PER_QUERY=256
TENDERWISE_QA_HISTORY_MESSAGES=8
NVIDIA_API_KEY=your_nvidia_api_key
NVIDIA_EMBEDDING_MODEL=nvidia/nemotron-3-embed-1b
```

Keep cross-encoder reranking disabled until dense retrieval is operating and its quality/latency have been measured. Hosted inference sends query/evidence text to the configured NVIDIA endpoint; only use it where your data-processing terms permit.

## Safe migration and rollout

1. Back up the existing database before changing schema.
2. Install the pgvector **PostgreSQL server extension** compatible with your PostgreSQL installation. The Python application package is not a replacement for the server extension. On Windows, follow the pgvector project's Windows build/install instructions for the exact PostgreSQL major version, or use a PostgreSQL provider that offers pgvector.
3. Check that the extension is available and enabled in the target database:

   ```sql
   SELECT extversion FROM pg_extension WHERE extname = 'vector';
   ```

   If it is installed but not yet enabled and your database role is permitted, run:

   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

4. From `backend` with the project virtual environment active, check the current revision and upgrade:

   ```powershell
   python -m alembic current
   python -m alembic upgrade head
   ```

5. Restart FastAPI and the document-processing worker.
6. Build chunks for already processed documents:

   ```powershell
   python -m app.services.retrieval_backfill
   ```

7. Generate vector embeddings for the existing chunks (requires valid `NVIDIA_API_KEY`):

   ```powershell
   python -m app.services.retrieval_backfill --embed --batch-size 64
   ```

   For one tender only:

   ```powershell
   python -m app.services.retrieval_backfill --tender-id YOUR-TENDER-UUID --embed
   ```

8. Check `GET /api/qa/retrieval-status`. The endpoint reports configuration readiness, not proof that all chunks have embeddings. For individual Q&A requests inspect `retrieval_mode`, `retrieval_trace.embedding_coverage`, candidate counts and per-tender metrics.

Migration `0011_advanced_rag_storage` is additive. It creates new tables and indexes; it does not remove the existing extraction data. It follows `0010_tender_milestones`. Do not run `alembic downgrade` on a production database as a casual rollback; take a backup and rehearse restoration first.

## Test and validation results

Validation performed in the build environment:

- Full backend pytest suite: **353 passed, 31 skipped, 2 warnings**.
  - 28 PostgreSQL integration cases skipped because `TENDERWISE_TEST_POSTGRESQL_URL` was not configured.
  - 1 LangGraph-related test skipped because `langgraph` was not installed in the isolated validation runtime (it is declared in `backend/requirements.txt`).
  - 2 Windows-specific filesystem tests skipped because the validation runtime was Linux.
- Python AST syntax check: **120 Python files, 0 syntax errors**.
- Frontend TS/TSX transpile syntax check: **26 files, 0 syntax diagnostics**.
- Alembic offline SQL generation: **passed**; generated SQL includes `CREATE EXTENSION vector`, `VECTOR(2048)`, `halfvec(2048)` HNSW expression index and all new tables.
- PostgreSQL SQLAlchemy vector/HALFVEC query compilation: **passed**.
- Query scan limits are applied at SQL level (after the per-document latest-successful-run and legacy-chunk detection queries), rather than fetching the full chunk corpus and slicing it in application memory.
- Full TypeScript typecheck: **not completed** (frontend dependencies/@types are not installed in this build environment).
- Vitest frontend suite: **not completed** (frontend dependencies are not installed).
- Vite production build: **not completed** (frontend dependencies are not installed).
- Live PostgreSQL/pgvector migration and ANN query: **not run** (no PostgreSQL server or `psql` tooling available in this environment).
- Live NVIDIA embedding/reranker requests: **not run** (no real provider credentials/network validation used).
- Real retrieval benchmark: **not run** (no human-labelled gold dataset provided).

The two backend warnings originate from tests that deliberately create malformed DOCX/XLSX ZIP archives with duplicate required members.

## Known limitations and next validation

- Rule-based topic query expansion is implemented; general LLM query rewriting is not.
- The reranker is optional and requires the NVIDIA hosted service and credentials.
- The claim-to-source check uses lexical overlap and exact numeric-token comparison; it is not a semantic entailment verifier.
- The evaluation runner scores retrieval rankings only; answer faithfulness and claim correctness need a separate human-labelled evaluation.
- Existing chunks are lazily backfilled on Q&A requests, but for larger libraries run the embedding backfill command before relying on broad dense recall.
- Authentication and tender-level authorization are not yet implemented in this application. Do not expose these endpoints to untrusted users or the public internet without adding access controls.
