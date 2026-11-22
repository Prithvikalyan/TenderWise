
"""Reusable NVIDIA NIM chat completion service."""

import json
import logging
import math
from typing import Any, Iterator

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(
    connect=30.0,
    read=300.0,
    write=60.0,
    pool=30.0,
)


class AIServiceError(Exception):
    """Safe AI provider error."""


class EmbeddingServiceError(AIServiceError):
    """Raised when an embedding request fails or returns invalid data."""


def _parse_embedding_response(payload: Any, expected_count: int) -> list[list[float]]:
    """Validate and order an NVIDIA embedding response by its ``index`` field.

    NVIDIA's embeddings endpoint may return vectors in a different order from
    the submitted inputs. We therefore place each vector at its declared index
    and reject missing, duplicate, malformed, non-finite, or inconsistent data.
    """
    if not isinstance(expected_count, int) or isinstance(expected_count, bool) or expected_count < 0:
        raise ValueError("expected_count must be a non-negative integer")
    if expected_count == 0:
        return []
    if not isinstance(payload, dict):
        raise EmbeddingServiceError("Embedding provider returned an invalid response.")

    data = payload.get("data")
    if not isinstance(data, list) or len(data) != expected_count:
        raise EmbeddingServiceError("Embedding provider returned an incomplete response.")

    ordered: list[list[float] | None] = [None] * expected_count
    dimensions: int | None = None

    for item in data:
        if not isinstance(item, dict):
            raise EmbeddingServiceError("Embedding provider returned an invalid vector entry.")

        index = item.get("index")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
            or index >= expected_count
            or ordered[index] is not None
        ):
            raise EmbeddingServiceError("Embedding provider returned invalid vector indexes.")

        raw_vector = item.get("embedding")
        if not isinstance(raw_vector, list) or not raw_vector:
            raise EmbeddingServiceError("Embedding provider returned an empty or invalid vector.")

        vector: list[float] = []
        for value in raw_vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EmbeddingServiceError("Embedding provider returned a non-numeric vector value.")
            number = float(value)
            if not math.isfinite(number):
                raise EmbeddingServiceError("Embedding provider returned a non-finite vector value.")
            vector.append(number)

        if dimensions is None:
            dimensions = len(vector)
        elif len(vector) != dimensions:
            raise EmbeddingServiceError("Embedding provider returned vectors with inconsistent dimensions.")

        ordered[index] = vector

    if any(vector is None for vector in ordered):
        raise EmbeddingServiceError("Embedding provider returned an incomplete set of vectors.")

    return [vector for vector in ordered if vector is not None]


def generate_embeddings(
    texts: list[str],
    *,
    input_type: str,
    model: str | None = None,
) -> list[list[float]]:
    """Generate NVIDIA NIM embeddings for query or passage text batches.

    ``input_type`` is forwarded to the NVIDIA endpoint so the model can apply
    its query- or passage-specific embedding mode.
    """
    if input_type not in {"query", "passage"}:
        raise ValueError("input_type must be either 'query' or 'passage'")
    if not isinstance(texts, list):
        raise ValueError("texts must be a list of strings")
    if not texts:
        return []
    if any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ValueError("texts must contain non-empty strings")

    settings = get_settings()
    api_key = getattr(settings, "nvidia_api_key", None)
    if not api_key:
        raise EmbeddingServiceError("AI provider is not configured for embeddings.")

    selected_model = model or getattr(
        settings,
        "nvidia_embedding_model",
        "nvidia/nemotron-3-embed-1b",
    )
    base_url = getattr(settings, "nvidia_base_url", "https://integrate.api.nvidia.com/v1")
    url = f"{base_url.rstrip('/')}/embeddings"
    payload: dict[str, Any] = {
        "input": texts,
        "input_type": input_type,
        "model": selected_model,
    }

    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            response = client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )

        if response.status_code >= 400:
            logger.error(
                "NVIDIA embedding HTTP error. model=%s status=%s body=%s",
                selected_model,
                response.status_code,
                getattr(response, "text", "")[:1000],
            )
            raise EmbeddingServiceError("Embedding provider could not process the request.")

        try:
            data = response.json()
        except ValueError:
            raise EmbeddingServiceError("Embedding provider returned an invalid response.") from None

        return _parse_embedding_response(data, len(texts))

    except EmbeddingServiceError:
        raise
    except httpx.TimeoutException:
        logger.exception("NVIDIA embedding request timed out. model=%s", selected_model)
        raise EmbeddingServiceError("Embedding provider request timed out. Please try again.") from None
    except httpx.HTTPError:
        logger.exception("NVIDIA embedding request failed. model=%s", selected_model)
        raise EmbeddingServiceError("Embedding service is temporarily unavailable.") from None
    except (KeyError, IndexError, TypeError):
        logger.exception("Unexpected NVIDIA embedding response. model=%s", selected_model)
        raise EmbeddingServiceError("Embedding provider returned an unexpected response.") from None


def generate_response_stream(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 700,
    temperature: float = 0.2,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> Iterator[str]:
    """Yield text deltas from an OpenAI-compatible NVIDIA NIM stream.

    The standard completion function remains unchanged for agent and report
    operations. Callers must validate the assembled response before saving it.
    """
    settings = get_settings()
    if not settings.nvidia_api_key:
        raise AIServiceError("AI provider is not configured.")

    selected_model = model or settings.nvidia_model
    url = f"{settings.nvidia_base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": selected_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    if reasoning_effort is not None:
        if reasoning_effort not in {"none", "low", "high"}:
            raise ValueError("Invalid reasoning_effort")
        payload["reasoning_effort"] = reasoning_effort
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            with client.stream(
                "POST",
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.nvidia_api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    logger.error(
                        "NVIDIA streaming HTTP error. model=%s status=%s",
                        selected_model,
                        response.status_code,
                    )
                    raise AIServiceError("AI provider could not process the request.")

                received_content = False
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue

                    raw_data = line[5:].strip()
                    if raw_data == "[DONE]":
                        break

                    try:
                        data = json.loads(raw_data)
                    except json.JSONDecodeError:
                        logger.warning("Ignoring malformed NVIDIA stream event. model=%s", selected_model)
                        continue

                    if not isinstance(data, dict):
                        continue
                    if data.get("error"):
                        logger.error("NVIDIA stream returned an error. model=%s", selected_model)
                        raise AIServiceError("AI provider interrupted the response.")

                    choices = data.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    choice = choices[0]
                    if not isinstance(choice, dict):
                        continue
                    delta = choice.get("delta")
                    if not isinstance(delta, dict):
                        continue

                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        received_content = True
                        yield content
                    elif isinstance(content, list):
                        parts = [
                            item.get("text", "")
                            for item in content
                            if isinstance(item, dict) and isinstance(item.get("text"), str)
                        ]
                        combined = "".join(parts)
                        if combined:
                            received_content = True
                            yield combined

                if not received_content:
                    raise AIServiceError("AI provider returned an empty response.")

    except AIServiceError:
        raise
    except httpx.TimeoutException:
        logger.exception("NVIDIA streaming request timed out. model=%s", selected_model)
        raise AIServiceError("AI provider request timed out. Please try again.") from None
    except httpx.HTTPError:
        logger.exception("NVIDIA streaming request failed. model=%s", selected_model)
        raise AIServiceError("AI service is temporarily unavailable.") from None
    except (KeyError, IndexError, TypeError, ValueError):
        logger.exception("Unexpected NVIDIA streaming response. model=%s", selected_model)
        raise AIServiceError("AI provider returned an unexpected response.") from None


def _extract_message_content(message: Any) -> str:
    """Extract text from supported chat completion formats."""

    if not isinstance(message, dict):
        return ""

    content = message.get("content")

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []

        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)

        return "\n".join(parts).strip()

    text = message.get("text")

    if isinstance(text, str):
        return text.strip()

    return ""


def generate_response(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 700,
    temperature: float = 0.2,
    model: str | None = None,
    enable_thinking: bool | None = None,
    reasoning_effort: str | None = None,
) -> str:
    """
    Generate a completion through NVIDIA NIM.

    Optional model override allows different TenderWise agents
    to use different NVIDIA models without changing .env.

    Existing callers continue using settings.nvidia_model.
    """

    settings = get_settings()

    if not settings.nvidia_api_key:
        raise AIServiceError("AI provider is not configured.")

    selected_model = model or settings.nvidia_model

    url = (
        f"{settings.nvidia_base_url.rstrip('/')}"
        "/chat/completions"
    )

    payload: dict[str, Any] = {
        "model": selected_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }

    # Only send this setting when explicitly requested.
    # This avoids affecting existing Mistral Q&A requests.
    if enable_thinking is not None:
        payload["chat_template_kwargs"] = {
            "enable_thinking": enable_thinking
        }
        if reasoning_effort is not None:
            if reasoning_effort not in {"none", "low", "high"}:
                raise ValueError("Invalid reasoning_effort")
            payload["reasoning_effort"] = reasoning_effort
            
    logger.info(
        "Sending NVIDIA AI request. model=%s max_tokens=%s "
        "thinking=%s",
        selected_model,
        max_tokens,
        enable_thinking,
    )

    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            response = client.post(
                url,
                json=payload,
                headers={
                    "Authorization": (
                        f"Bearer {settings.nvidia_api_key}"
                    ),
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )

        if response.status_code >= 400:
            logger.error(
                "NVIDIA API HTTP error. model=%s status=%s body=%s",
                selected_model,
                response.status_code,
                response.text[:1000],
            )

            raise AIServiceError(
                "AI provider could not process the request."
            )

        try:
            data = response.json()
        except ValueError:
            logger.error(
                "NVIDIA returned invalid JSON. model=%s status=%s",
                selected_model,
                response.status_code,
            )
            raise AIServiceError(
                "AI provider returned an invalid response."
            ) from None

        if not isinstance(data, dict):
            raise AIServiceError(
                "AI provider returned an unexpected response."
            )

        choices = data.get("choices")

        if not isinstance(choices, list) or not choices:
            logger.error(
                "NVIDIA returned no choices. model=%s response_id=%s",
                selected_model,
                data.get("id"),
            )
            raise AIServiceError(
                "AI provider returned no completion choices."
            )

        choice = choices[0]

        if not isinstance(choice, dict):
            raise AIServiceError(
                "AI provider returned an invalid completion."
            )

        message = choice.get("message")
        finish_reason = choice.get("finish_reason")

        content = _extract_message_content(message)

        if not content:
            logger.error(
                "NVIDIA returned empty content. model=%s "
                "finish_reason=%s response_id=%s usage=%s",
                selected_model,
                finish_reason,
                data.get("id"),
                data.get("usage"),
            )

            raise AIServiceError(
                "AI provider returned empty content."
            )

        logger.info(
            "NVIDIA completion successful. model=%s "
            "finish_reason=%s response_id=%s usage=%s",
            selected_model,
            finish_reason,
            data.get("id"),
            data.get("usage"),
        )

        return content

    except AIServiceError:
        raise

    except httpx.TimeoutException:
        logger.exception(
            "NVIDIA AI request timed out. model=%s",
            selected_model,
        )
        raise AIServiceError(
            "AI provider request timed out. Please try again."
        ) from None

    except httpx.HTTPError:
        logger.exception(
            "NVIDIA HTTP request failed. model=%s",
            selected_model,
        )
        raise AIServiceError(
            "AI service is temporarily unavailable."
        ) from None

    except (KeyError, IndexError, TypeError):
        logger.exception(
            "Unexpected NVIDIA response parsing error. model=%s",
            selected_model,
        )
        raise AIServiceError(
            "AI provider returned an unexpected response."
        ) from None


def rerank_passages(
    query: str,
    passages: list[str],
    *,
    model: str | None = None,
    endpoint: str | None = None,
) -> list[tuple[int, float]]:
    """Rerank text passages with NVIDIA's hosted NeMo Retriever endpoint.

    Returns ``(original_index, logit)`` pairs in relevance order. The caller
    owns fallback behavior; provider errors are surfaced as AIServiceError.
    """
    if not query.strip() or not passages:
        return []
    if len(passages) > 50:
        raise ValueError("At most 50 passages may be reranked in one request")
    settings = get_settings()
    api_key = getattr(settings, "nvidia_api_key", None)
    if not api_key:
        raise AIServiceError("AI provider is not configured for reranking.")
    selected_model = model or getattr(settings, "tenderwise_reranker_model", "nvidia/nemotron-3.5-rerank-8b")
    selected_endpoint = endpoint or getattr(
        settings,
        "tenderwise_reranker_url",
        "https://ai.api.nvidia.com/v1/retrieval/nvidia/nemotron-3.5-rerank-8b/reranking",
    )
    payload = {
        "model": selected_model,
        "query": {"text": query},
        "passages": [{"text": passage} for passage in passages],
    }
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            response = client.post(
                selected_endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        if response.status_code >= 400:
            logger.warning("NVIDIA reranking returned HTTP %s", response.status_code)
            raise AIServiceError("The reranking provider could not process the request.")
        body = response.json()
        rankings = body.get("rankings") if isinstance(body, dict) else None
        if not isinstance(rankings, list):
            raise AIServiceError("The reranking provider returned an invalid response.")
        parsed: list[tuple[int, float]] = []
        seen: set[int] = set()
        for item in rankings:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            logit = item.get("logit")
            if (
                isinstance(index, bool) or not isinstance(index, int)
                or index < 0 or index >= len(passages) or index in seen
                or isinstance(logit, bool) or not isinstance(logit, (int, float))
                or not math.isfinite(float(logit))
            ):
                continue
            seen.add(index)
            parsed.append((index, float(logit)))
        if not parsed:
            raise AIServiceError("The reranking provider returned no usable passage rankings.")
        parsed.sort(key=lambda value: value[1], reverse=True)
        return parsed
    except AIServiceError:
        raise
    except httpx.TimeoutException:
        logger.warning("NVIDIA reranking request timed out")
        raise AIServiceError("The reranking provider timed out.") from None
    except httpx.HTTPError:
        logger.warning("NVIDIA reranking request failed")
        raise AIServiceError("The reranking provider is temporarily unavailable.") from None
    except (ValueError, TypeError, KeyError):
        logger.warning("NVIDIA reranking returned an invalid payload")
        raise AIServiceError("The reranking provider returned an invalid response.") from None
