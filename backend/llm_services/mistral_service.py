"""Groq chat and Voyage AI embedding service helpers."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langchain_voyageai import VoyageAIEmbeddings
import voyageai
from pydantic import SecretStr

try:
    from backend.config import (
        GROQ_API_KEY,
        GROQ_CHAT_MODEL,
        VOYAGEAI_API_KEY,
        VOYAGEAI_EMBEDDING_MODEL,
        MISTRAL_API_KEY,
        MISTRAL_EMBEDDING_MODEL,
        RAG_MAX_OUTPUT_TOKENS,
    )
except ModuleNotFoundError:
    from config import (
        GROQ_API_KEY,
        GROQ_CHAT_MODEL,
        VOYAGEAI_API_KEY,
        VOYAGEAI_EMBEDDING_MODEL,
        MISTRAL_API_KEY,
        MISTRAL_EMBEDDING_MODEL,
        RAG_MAX_OUTPUT_TOKENS,
    )


FALLBACK_RESPONSE = "I don't know based on the uploaded PDF."


def _require_embedding_key() -> SecretStr:
    key = VOYAGEAI_API_KEY or MISTRAL_API_KEY
    if not key:
        raise HTTPException(status_code=500, detail="VOYAGEAI_API_KEY is not configured")
    return SecretStr(key)


def _require_groq_key() -> SecretStr:
    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY is not configured")
    return SecretStr(GROQ_API_KEY)


@lru_cache(maxsize=1)
def get_voyage_client() -> voyageai.Client:
    key = _require_embedding_key()
    return voyageai.Client(api_key=key.get_secret_value())


@lru_cache(maxsize=1)
def get_embeddings_model() -> VoyageAIEmbeddings:
    return VoyageAIEmbeddings(
        voyage_api_key=_require_embedding_key(),
        model=VOYAGEAI_EMBEDDING_MODEL,
    )


@lru_cache(maxsize=1)
def get_chat_model() -> ChatGroq:
    return ChatGroq(
        groq_api_key=_require_groq_key(),
        model=GROQ_CHAT_MODEL,
        temperature=0.0,
        max_tokens=RAG_MAX_OUTPUT_TOKENS,
    )


def embed_documents(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    try:
        client = get_voyage_client()
        res = client.embed(texts, model=VOYAGEAI_EMBEDDING_MODEL, input_type="document")
        return res.embeddings
    except Exception as exc:
        try:
            return get_embeddings_model().embed_documents(texts)
        except Exception:
            raise HTTPException(status_code=502, detail=f"Voyage AI embedding failed: {str(exc)}")


def embed_query(text: str) -> list[float]:
    if not text:
        return []
    try:
        client = get_voyage_client()
        res = client.embed([text], model=VOYAGEAI_EMBEDDING_MODEL, input_type="query")
        return res.embeddings[0]
    except Exception as exc:
        try:
            return get_embeddings_model().embed_query(text)
        except Exception:
            raise HTTPException(status_code=502, detail=f"Voyage AI query embedding failed: {str(exc)}")


def _extract_response_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
        return "".join(text_parts)

    return ""


def generate_grounded_answer(question: str, context_chunks: list[dict]) -> str:
    if not context_chunks:
        return FALLBACK_RESPONSE

    context_lines: list[str] = []
    for item in context_chunks:
        context_lines.append(
            f"[Chunk {item.get('chunk_index', 0) + 1} | Score {item.get('score', 0.0):.3f}] {item.get('text', '')}"
        )

    system_prompt = (
        "You are a policy assistant. Answer strictly from the provided PDF context. "
        "Never use outside knowledge. Never infer missing facts. "
        "Format your response using markdown with:\n"
        "- Use **bold** for key terms and headers\n"
        "- Use bullet points (- or *) for lists\n"
        "- Use proper sections and structure\n"
        "- Use line breaks between sections for readability\n"
        "If the answer is not explicitly present in the context, reply exactly: "
        f'"{FALLBACK_RESPONSE}"'
    )
    user_prompt = (
        f"Question: {question}\n\n"
        f"Context:\n{chr(10).join(context_lines)}\n\n"
        "Instruction: Return only a concise, grounded answer from context."
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]
    try:
        response = get_chat_model().invoke(messages)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {str(exc)}")

    answer = _extract_response_text(getattr(response, "content", "")).strip()
    if not answer:
        return FALLBACK_RESPONSE
    return answer
