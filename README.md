# TenderWise

**AI-powered government tender intelligence and bid-readiness platform**

TenderWise helps teams review government tender documents, understand
bidder requirements, organize company evidence, and prepare for bid
submission from a single tender workspace.

> TenderWise is a decision-support tool. AI-generated findings must be
> checked against the original tender documents. It does not guarantee
> eligibility, bid acceptance, or contract award.


## Application Screenshots

### Dashboard
![TenderWise Dashboard](docs/screenshots/01-dashboard.png)

### Tender Workspace
![Tender Workspace](docs/screenshots/02-tender-workspace.png)

### Tender Analysis
![Tender Analysis](docs/screenshots/03-tender-analysis.png)

### Compliance Analysis
![Compliance Analysis](docs/screenshots/04-compliance-analysis.png)

### Company Document Management
![Company Documents](docs/screenshots/05-company-documents.png)

### Evidence Matching
![Evidence Matching](docs/screenshots/06-evidence-matching.png)

### Tender Q&A
![Tender Q&A](docs/screenshots/07-tender-qa.png)

### Reports and Milestones
![Reports and Milestones](docs/screenshots/08-reports-milestones.png)


If you use different filenames, update the Markdown image paths. GitHub
displays the images once those files are committed.

## What TenderWise does

-   **Tender workspaces:** Create and manage separate workspaces for
    individual tenders.
-   **Document management:** Upload, categorize, process, list,
    download, and remove tender documents.
-   **Tender analysis:** Review scope, dates, BOQ information,
    commercial terms, and potential risks extracted from tender
    material.
-   **Eligibility analysis:** Identify bidder qualification criteria and
    the evidence requested by the tender.
-   **Compliance analysis:** Organize submission, technical, and
    post-award requirements.
-   **Company document repository:** Maintain reusable company documents
    and their versions.
-   **Company evidence matching:** Match tender requirements against
    available company evidence, review candidate documents, and identify
    missing, expired, pending, or manually verifiable evidence.
-   **Saved matching assessments:** Reopen saved company
    evidence-matching runs for a tender and company.
-   **Bid-readiness reporting:** Review open actions and manual
    verification items, and print or save the report as PDF from the
    browser.
-   **Tender Q&A:** Ask questions about uploaded tender material and
    inspect cited source references. Tender-specific conversations can
    be saved and reopened.
-   **Citation source viewer:** Open the original source document from
    supported citations; PDF citations navigate to the referenced page.
-   **Tender comparison:** Compare up to three tender workspaces using
    available workspace metadata.
-   **Milestone calendar:** Record manually confirmed tender dates such
    as submission deadlines, clarification dates, and pre-bid meetings.

### Important scope notes

-   Tender comparison currently compares workspace metadata; it is
    **not** an AI comparison of all technical or financial tender
    clauses.
-   The source viewer navigates to a cited PDF page but does not
    highlight the exact sentence inside the PDF.
-   The current document extraction worker actively extracts PDF text
    and tables. OCR and the full extraction pipeline for other document
    formats are not represented as supported capabilities in this
    release.
-   Tender milestones are entered and confirmed by the user. TenderWise
    does not invent official dates.

## Advanced RAG approach

TenderWise uses a tender-scoped retrieval pipeline designed to preserve
source context:

1.  **Structure-aware chunking** builds retrieval passages from
    extracted document units. Text chunks retain heading context and
    source locations. Table chunks retain column headings alongside
    rows.
2.  **BM25 retrieval** finds lexical matches.
3.  **Dense retrieval** searches vector embeddings stored in PostgreSQL
    with pgvector.
4.  **Query decomposition** adds focused searches for recognized tender
    topics such as EMD, experience, turnover, and deadlines.
5.  **Reciprocal Rank Fusion (RRF)** combines lexical and dense result
    rankings.
6.  **Optional reranking** can use the NVIDIA Nemotron 3.5 Rerank model
    to rerank fused candidates. It is disabled by default.
7.  **Source-aware answers** retain document and location references.
    Citation IDs are checked against the supplied source set, and
    factual claims receive a conservative lexical-overlap and
    numeric-value review.

The claim review is a heuristic, not a semantic entailment guarantee. A
citation or text-overlap result does not prove that a statement is
legally correct or that a bidder satisfies a tender condition. Human
verification against the original tender remains necessary.

### Retrieval configuration defaults

-   Embedding model: `nvidia/nemotron-3-embed-1b`
-   Embedding dimensions: 2,048
-   Chunk target: 600 tokens
-   Chunk overlap: 80 tokens
-   Candidate retrieval: 50
-   RRF constant: 60
-   HNSW `ef_search`: 100
-   Reranker: disabled by default

## Technology stack

  -----------------------------------------------------------------------
  Layer                               Technologies
  ----------------------------------- -----------------------------------
  Frontend                            React, TypeScript, Vite

  Backend API                         Python, FastAPI, Pydantic

  Database                            PostgreSQL, SQLAlchemy, Alembic

  Vector search                       pgvector, HNSW

  AI / LLM integration                NVIDIA API using an
                                      OpenAI-compatible interface

  Workflow orchestration              LangGraph

  Document extraction                 PyMuPDF, pdfplumber

  Testing                             pytest, Vitest, React Testing
                                      Library
  -----------------------------------------------------------------------

## Architecture overview

``` text
React + TypeScript + Vite
          |
          | HTTP / JSON
          v
      FastAPI API  <--------------------> PostgreSQL
          |                                  |
          |                                  +-- Tender and company records
          |                                  +-- Documents and extraction units
          |                                  +-- Retrieval chunks and embeddings
          |                                  +-- Saved Q&A conversations
          |                                  +-- Evidence matching runs
          |                                  +-- Milestones and retrieval telemetry
          |
          +---- Document processing queue
                         |
                         v
                 Background worker
                         |
                         +-- PDF text extraction
                         +-- PDF table extraction
                         +-- Structure-aware retrieval chunking
                         |
                         v
                  NVIDIA API
             (embeddings / generation /
                optional reranking)
```

## Prerequisites

-   Python 3.10 or newer
-   Node.js 22.22.2+ or 24.15+
-   PostgreSQL 14+ (PostgreSQL with pgvector is required for dense
    retrieval)
-   pgvector server extension 0.7.0 or newer
-   NVIDIA API key for hosted AI features

The PostgreSQL **server extension** must be installed for the PostgreSQL
version in use. Installing a Python package does not install the server
extension.

## Getting started (Windows PowerShell)

### 1. Clone the repository

``` powershell
git clone https://github.com/<your-username>/<your-repository>.git
cd <your-repository>
```

### 2. Create and activate the Python environment

``` powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
```

If `py` is unavailable, use `python` instead.

### 3. Configure environment variables

Copy the example configuration:

``` powershell
Copy-Item .env.example .env
```

Edit `.env` and set at least your PostgreSQL connection string:

``` dotenv
DATABASE_URL=postgresql+psycopg://postgres:DB_PASSWORD@localhost:5432/tenderwise
```

For AI-powered analysis, embeddings, and Q&A, also set:

``` dotenv
NVIDIA_API_KEY=your_nvidia_api_key
```

The `.env.example` file contains the available RAG configuration
options. The default configuration enables semantic retrieval when the
database, pgvector extension, and NVIDIA credentials are available. The
optional reranker is disabled by default.

**Never commit `.env`, API keys, database passwords, uploaded tender
files, or private company documents.**

### 4. Prepare PostgreSQL and pgvector

Create the `tenderwise` database, then ensure the pgvector extension is
installed and available on the PostgreSQL server.

Connect to the database and run:

``` sql
CREATE EXTENSION IF NOT EXISTS vector;
SELECT extversion FROM pg_extension WHERE extname = 'vector';
```

Use pgvector 0.7.0 or newer. Back up an existing database before
applying migrations.

### 5. Apply database migrations

From the repository root:

``` powershell
cd backend
python -m alembic current
python -m alembic upgrade head
```

The advanced retrieval schema is introduced by migration
`0011_advanced_rag_storage`. It adds retrieval chunks, chunk embeddings,
saved Q&A conversations/messages, and retrieval telemetry.

Return to the repository root when needed:

``` powershell
cd ..
```

### 6. Start the FastAPI backend

In a terminal with the virtual environment active:

``` powershell
cd backend
python -m uvicorn app.main:app --reload
```

The API is available at:

-   Application: `http://127.0.0.1:8000`
-   Swagger API docs: `http://127.0.0.1:8000/docs`
-   Health check: `http://127.0.0.1:8000/api/health`
-   Database health: `http://127.0.0.1:8000/api/health/database`

Keep this terminal running.

### 7. Start the document-processing worker

Open a second terminal:

``` powershell
cd <repository-folder>
.\.venv\Scripts\Activate.ps1
cd backend
python -m app.worker
```

The worker runs independently from FastAPI. Keep it running while
processing documents.

### 8. Start the frontend

Open a third terminal:

``` powershell
cd <repository-folder>\frontend
npm ci
npm run dev
```

Open the local URL printed by Vite, typically `http://localhost:5173/`.

The Vite development server proxies `/api` requests to
`http://127.0.0.1:8000`.

## Backfill existing documents for advanced retrieval

Newly processed documents create retrieval chunks as part of processing.
To create chunks for documents processed before the advanced retrieval
upgrade, run from the `backend` directory:

``` powershell
python -m app.services.retrieval_backfill
```

To generate embeddings for the backfilled chunks (requires a valid
`NVIDIA_API_KEY` and PostgreSQL with pgvector):

``` powershell
python -m app.services.retrieval_backfill --embed --batch-size 64
```

For a single tender:

``` powershell
python -m app.services.retrieval_backfill --tender-id YOUR-TENDER-UUID --embed
```

## Verification and tests

### Backend

From the `backend` directory with the virtual environment active:

``` powershell
python -m pytest
```

### Frontend

From the `frontend` directory:

``` powershell
npm run typecheck
npm test
npm run build
```

PostgreSQL integration tests require a dedicated disposable test
database. Do not point test database settings at a development, shared,
or production database. Tests that are skipped because no PostgreSQL
test URL is configured have not validated live PostgreSQL behavior.

## Security and data handling

-   Keep secrets in `.env` and exclude it from version control.
-   Do not commit uploaded tender documents, company evidence, local
    storage folders, or database dumps.
-   Treat tender documents and company evidence as confidential business
    information.
-   Hosted AI calls send the configured prompt, question, or evidence
    text to the configured NVIDIA endpoint. Review applicable
    data-processing terms and your organization's policies before
    sending confidential material.
-   Restrict database access and use a dedicated database role with only
    the permissions required by the application.
-   Back up PostgreSQL before migrations and important data changes.

## Repository screenshots

Suggested layout:

``` text
docs/
â””â”€â”€ screenshots/
    â”œâ”€â”€ dashboard.png
    â”œâ”€â”€ tender-workspace.png
    â”œâ”€â”€ document-processing.png
    â”œâ”€â”€ tender-qa.png
    â”œâ”€â”€ company-evidence.png
    â””â”€â”€ bid-readiness.png
```

## Current limitations

-   AI extraction and analysis can miss or misinterpret content. Always
    verify important values, clauses, dates, and eligibility decisions
    against the source tender.
-   PDF extraction does not perform OCR. Image-only or scanned PDFs may
    produce no usable text unless OCR is added separately.
-   The current worker extraction implementation is focused on PDF text
    and table extraction.
-   Dense retrieval requires PostgreSQL, pgvector, and configured
    embedding credentials. Keyword fallback remains available when
    semantic retrieval is unavailable or disabled.
-   Optional cross-encoder reranking is disabled by default and should
    be enabled only after evaluating its latency and retrieval quality.
-   The retrieval evaluation runner is included, but meaningful quality
    metrics require a manually reviewed, representative gold dataset. No
    benchmark score is claimed by this README.

## Contributing

Issues and pull requests are welcome. When reporting a bug, include:

-   A clear description of the problem
-   Steps to reproduce it
-   Relevant logs with secrets and private tender data removed
-   Expected and actual behavior

For changes to retrieval behavior, include representative test cases and
source-grounded evaluation examples where possible.

## License

No license has been specified in this repository yet. Add a `LICENSE`
file before presenting the project as open source. Until then, do not
assume that others have permission to reuse, modify, or distribute the
code.


