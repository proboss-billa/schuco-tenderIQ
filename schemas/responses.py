"""Pydantic models for response structure."""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class ParameterResponse(BaseModel):
    parameter_name: str
    parameter_key: str
    value: str | None
    unit: str | None
    confidence: float | None
    source: dict | None
    sources: list
    notes: str | None
    source_text: str | None
    multi_source: bool


class DocumentInfo(BaseModel):
    document_id: str
    filename: str
    file_type: str
    processing_status: str
    processing_error: str | None = None
    page_count: int | None = None
    num_chunks: int


class ExtractedParametersResponse(BaseModel):
    project_id: str
    project_type: str
    processing_status: str
    pipeline_step: str | None
    parameters: list[ParameterResponse]
    total_extracted: int
    documents: list
