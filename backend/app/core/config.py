from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application settings."""

    database_url: str | None = None
    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "mistralai/mistral-large-4-1t-a52b"
    nvidia_embedding_model: str = "nvidia/nemotron-3-embed-1b"
    tenderwise_semantic_retrieval_enabled: bool = True
    tenderwise_reranker_enabled: bool = False
    tenderwise_reranker_model: str = "nvidia/nemotron-3.5-rerank-8b"
    tenderwise_reranker_url: str = "https://ai.api.nvidia.com/v1/retrieval/nvidia/nemotron-3.5-rerank-8b/reranking"
    tenderwise_chunk_target_tokens: int = Field(default=600, ge=200, le=1200)
    tenderwise_chunk_overlap_tokens: int = Field(default=80, ge=0, le=200)
    tenderwise_qa_history_messages: int = Field(default=8, ge=0, le=20)
    tenderwise_qa_max_vector_backfill_per_query: int = Field(default=256, ge=0, le=2000)
    tenderwise_qa_candidate_k: int = Field(default=50, ge=5, le=200)
    tenderwise_qa_rrf_k: int = Field(default=60, ge=1, le=200)
    tenderwise_qa_hnsw_ef_search: int = Field(default=100, ge=40, le=1000)
    nvidia_timeout_seconds: int = Field(default=90, ge=10, le=180)
    tenderwise_storage_dir: Path = Path(__file__).resolve().parents[3] / ".tenderwise_storage"
    tenderwise_max_file_size_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    tenderwise_max_batch_size_bytes: int = Field(default=200 * 1024 * 1024, gt=0)
    tenderwise_max_pdf_pages: int = Field(default=500, ge=1, le=500)
    tenderwise_max_extraction_result_bytes: int = Field(default=25 * 1024 * 1024, ge=1, le=25 * 1024 * 1024)
    tenderwise_max_extraction_text_unit_bytes: int = Field(default=256 * 1024, ge=1, le=256 * 1024)
    # Strictly tried after UTF-8/UTF-8 BOM for legacy CSV exports.
    tenderwise_csv_fallback_encodings: str = "cp1252"
    tenderwise_processing_max_documents_per_run: int = Field(default=20, ge=1, le=20)
    tenderwise_worker_max_processes: int = Field(default=2, ge=1, le=2)
    tenderwise_worker_poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=60.0)
    tenderwise_processing_lease_seconds: int = Field(default=120, ge=30, le=600)
    tenderwise_processing_heartbeat_seconds: int = Field(default=30, ge=1, le=120)
    tenderwise_processing_timeout_seconds: int = Field(default=900, ge=30, le=1800)
    tenderwise_processing_max_attempts: int = Field(default=3, ge=1, le=3)
    tenderwise_processing_retry_base_seconds: float = Field(default=5.0, ge=0.1, le=60.0)
    tenderwise_processing_retry_max_seconds: float = Field(default=300.0, ge=1.0, le=1800.0)

    @model_validator(mode="after")
    def validate_worker_timing(self) -> "Settings":
        if self.tenderwise_processing_heartbeat_seconds >= self.tenderwise_processing_lease_seconds / 3:
            raise ValueError("Heartbeat interval must be less than one third of the lease duration")
        if self.tenderwise_processing_retry_base_seconds > self.tenderwise_processing_retry_max_seconds:
            raise ValueError("Retry base delay must not exceed the retry maximum")
        return self

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
