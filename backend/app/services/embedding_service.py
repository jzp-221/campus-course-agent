"""OpenAI-compatible embeddings, supplied only by the configured provider."""

import math

import httpx

from app.common.exceptions import BusinessException
from app.config import settings

BATCH_SIZE = 32
TIMEOUT_SECONDS = 30


def embed_texts(texts: list[str], *, config=None, client_factory=None) -> list[list[float]]:
    config = settings if config is None else config
    key = config.EMBEDDING_API_KEY.get_secret_value().strip()
    # Even an empty knowledge base must report this configuration error.
    if not key:
        raise BusinessException("检索失败：未配置 EMBEDDING_API_KEY", code=503)
    if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
        raise BusinessException("检索失败：embedding 输入不能为空", code=400)
    url = config.EMBEDDING_BASE_URL.rstrip("/") + "/embeddings"
    factory = httpx.Client if client_factory is None else client_factory
    vectors = []
    dimension = None
    try:
        with factory(timeout=TIMEOUT_SECONDS, trust_env=False) as client:
            for start in range(0, len(texts), BATCH_SIZE):
                batch = texts[start:start + BATCH_SIZE]
                response = client.post(
                    url,
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": config.EMBEDDING_MODEL, "input": batch, "encoding_format": "float"},
                )
                response.raise_for_status()
                data = response.json()["data"]
                if not isinstance(data, list) or len(data) != len(batch):
                    raise ValueError("invalid number of vectors")
                ordered = {}
                for item in data:
                    index, vector = item["index"], item["embedding"]
                    if type(index) is not int or index not in range(len(batch)) or index in ordered:
                        raise ValueError("invalid vector index")
                    if not isinstance(vector, list) or not vector:
                        raise ValueError("missing vector")
                    if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
                        raise ValueError("invalid vector values")
                    if not any(value != 0 for value in vector):
                        raise ValueError("zero vector")
                    if dimension is None:
                        dimension = len(vector)
                    if len(vector) != dimension:
                        raise ValueError("inconsistent vector dimension")
                    ordered[index] = [float(value) for value in vector]
                vectors.extend(ordered[index] for index in range(len(batch)))
    except httpx.HTTPStatusError as error:
        # Do not forward provider response bodies, credentials, or request headers.
        raise BusinessException(
            f"检索失败：embedding 服务返回 HTTP {error.response.status_code}", code=502
        ) from error
    except httpx.HTTPError as error:
        raise BusinessException("检索失败：embedding 服务连接失败或超时", code=502) from error
    except (ValueError, KeyError, TypeError, IndexError) as error:
        raise BusinessException("检索失败：embedding 服务返回了无效向量", code=502) from error
    return vectors
