"""Small SQLAlchemy type adapter for the PostgreSQL pgvector extension.

The application stores full float vectors in PostgreSQL's ``vector`` type and
uses an expression-level ``halfvec`` cast for the HNSW index (the configured
2048-dimensional embedding is above pgvector's 2,000-dimensional HNSW limit for
full-precision vector indexes). SQLite tests use JSON storage and never execute
vector operators. Keeping this adapter local also means the test suite can run
without loading PostgreSQL's optional driver adapters.
"""

from __future__ import annotations

import json
import math
from typing import Any

from sqlalchemy import JSON, cast, literal
from sqlalchemy.types import TypeDecorator, UserDefinedType


def vector_literal(value: Any) -> str:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("A non-empty vector is required")
    numbers: list[str] = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise ValueError("Vector components must be numeric")
        number = float(component)
        if not math.isfinite(number):
            raise ValueError("Vector components must be finite")
        numbers.append(format(number, ".9g"))
    return "[" + ",".join(numbers) + "]"


class _PostgresVector(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions: int):
        self.dimensions = dimensions

    def get_col_spec(self, **_kw: Any) -> str:
        return f"VECTOR({self.dimensions})"


class _PostgresHalfVector(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions: int):
        self.dimensions = dimensions

    def get_col_spec(self, **_kw: Any) -> str:
        return f"HALFVEC({self.dimensions})"


class Vector(TypeDecorator):
    """A fixed-dimension vector that maps to JSON only in SQLite tests."""

    impl = JSON
    cache_ok = True

    def __init__(self, dimensions: int):
        super().__init__()
        self.dimensions = dimensions

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PostgresVector(self.dimensions))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if len(value) != self.dimensions:
            raise ValueError(f"Expected a {self.dimensions}-dimensional embedding")
        if dialect.name == "postgresql":
            return vector_literal(value)
        # JSON round-trip catches unsupported values before SQLite persistence.
        return json.loads(json.dumps(value, allow_nan=False))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            if value.startswith("["):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    try:
                        return [float(part) for part in value.strip("[]").split(",") if part]
                    except ValueError:
                        return None
        return value

    class comparator_factory(TypeDecorator.Comparator):
        def cosine_distance(self, other):
            vector_type = _PostgresHalfVector(self.type.dimensions)
            target = cast(literal(vector_literal(other)), vector_type)
            source = cast(self.expr, vector_type)
            return source.op("<=>")(target)


__all__ = ["Vector", "_PostgresHalfVector", "vector_literal"]
