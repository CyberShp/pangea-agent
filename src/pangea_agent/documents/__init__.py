"""Deterministic document extraction for local evidence."""

from .extract import DependencyUnavailableError, extract_document
from .types import DocumentExtraction, EvidenceAttachment

__all__ = [
    "DependencyUnavailableError",
    "DocumentExtraction",
    "EvidenceAttachment",
    "extract_document",
]
