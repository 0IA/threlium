"""Узкая граница типов ответов LiteLLM для прод-вызовов без streaming.

См. ``litellm.main``:

* ``completion`` / ``acompletion`` аннотированы как
  ``Union[ModelResponse, CustomStreamWrapper]``. Здесь ожидается только
  ``ModelResponse`` (вызовы с ``stream=False`` — как в
  ``threlium.states.reasoning`` и runner ``lightrag``).

* ``aembedding`` → ``EmbeddingResponse``; элементы ``resp.data`` после
  :func:`require_embedding_response` — всегда :class:`litellm.types.utils.Embedding`
  (LiteLLM кладёт в ``data`` сырые ``dict`` после ``model_dump`` / конвертера).

Форма ``ModelResponse`` / ``Embedding`` совпадает с e2e CustomLLM
``tests/e2e/reference_l0/threlium_e2e_l0.py`` (объекты
``litellm.types.utils``).
"""
from __future__ import annotations

from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.types.utils import Embedding, EmbeddingResponse, ModelResponse
from pydantic import TypeAdapter

_embedding_rows_ta = TypeAdapter(list[Embedding])


def require_chat_model_response(resp: object) -> ModelResponse:
    """Сузить ответ chat completion до ``ModelResponse`` (не stream)."""
    if isinstance(resp, CustomStreamWrapper):
        raise TypeError(
            "expected ModelResponse with stream=False; got CustomStreamWrapper"
        )
    if isinstance(resp, ModelResponse):
        return resp
    raise TypeError(f"expected ModelResponse; got {type(resp).__name__!r}")


def require_embedding_response(resp: object) -> EmbeddingResponse:
    """Сузить ответ embedding до ``EmbeddingResponse`` с типизированным ``data``."""
    if isinstance(resp, EmbeddingResponse):
        raw = list(resp.data or [])
        resp.data = _embedding_rows_ta.validate_python(raw)
        return resp
    raise TypeError(f"expected EmbeddingResponse; got {type(resp).__name__!r}")
