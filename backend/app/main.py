from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from app.api.routes.companies import router as companies_router
from app.api.routes.company_processing import router as company_processing_router
from app.api.routes.health import router as health_router
from app.api.routes.documents import router as documents_router
from app.api.routes.processing import router as processing_router
from app.api.routes.tenders import router as tenders_router
from app.api.routes import analysis, compliance, eligibility, evidence_matching, milestones, qa, report, verification

app = FastAPI(title="TenderWise API", version="0.1.0")


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(_request: Request, _exc: SQLAlchemyError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "Database operation failed"},
    )


app.include_router(health_router, prefix="/api")
app.include_router(tenders_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(processing_router, prefix="/api")
app.include_router(qa.router, prefix="/api")
app.include_router(qa.diagnostics_router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(eligibility.router, prefix="/api")
app.include_router(compliance.router, prefix="/api")
app.include_router(evidence_matching.router, prefix="/api")
app.include_router(report.router, prefix="/api")
app.include_router(verification.router, prefix="/api")
app.include_router(milestones.router, prefix="/api")
app.include_router(companies_router, prefix="/api")
app.include_router(company_processing_router, prefix="/api")
