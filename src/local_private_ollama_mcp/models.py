"""Typed inputs and outputs of the MCP tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

FileType = Literal["pdf", "markdown", "text"]
ReferenceKind = Literal[
    "journal_article", "conference_paper", "book", "book_chapter", "thesis", "report",
    "preprint", "standard", "patent", "web_resource", "dataset_or_software", "other",
]


class CharRange(BaseModel):
    """A [start, end) character range of the document text, e.g. a section's range
    from document_overview."""

    start: int = Field(ge=0, description="First character (inclusive).")
    end: int = Field(gt=0, description="Last character (exclusive).")

    @model_validator(mode="after")
    def _ordered(self) -> CharRange:
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class Processing(BaseModel):
    """How much of the document the local model read and how."""

    range_start: int = Field(description="Start of the processed character range.")
    range_end: int = Field(description="End of the processed character range (exclusive).")
    characters: int = Field(description="Number of characters processed.")
    chunks: int = Field(description="Number of chunks the text was split into (1 = read at once).")
    condensed: bool = Field(description="True if the text was condensed into notes before the final step, losing detail.")
    truncated: bool = Field(description="True if text had to be cut to fit the context window.")


class DocumentEntry(BaseModel):
    name: str
    file_type: FileType
    size_bytes: int


class DocumentList(BaseModel):
    documents: list[DocumentEntry]


class Section(BaseModel):
    index: int = Field(description="Position in the table of contents.")
    level: int = Field(description="Heading level, 1 = top level.")
    title: str
    start: int = Field(description="First character of the section (inclusive).")
    end: int = Field(description="End of the section including its subsections (exclusive).")
    characters: int


class DocumentOverview(BaseModel):
    name: str
    file_type: FileType
    size_bytes: int
    sha256: str
    title: str | None = Field(description="PDF metadata title or first top-level heading, if any.")
    pages: int | None = Field(description="Page count for PDFs.")
    characters: int
    words: int
    fits_in_context: bool = Field(description="Whether the whole document fits the local model's context at once.")
    chunks_needed: int = Field(description="Chunks the local model needs to read the whole document.")
    context_budget_chars: int = Field(
        description="Characters the local model can read in one go; ranges up to this size are not condensed."
    )
    conversion_cached: bool = Field(description="For PDFs: the markdown conversion came from the cache.")
    toc_method: Literal["markdown", "pdf_outline", "heuristic", "none"] = Field(
        description="How the table of contents was derived; 'heuristic' may miss or misdetect headings."
    )
    sections: list[Section]
    sections_truncated: bool = Field(description="True if the table of contents was cut to the first 500 entries.")


class ServerStatus(BaseModel):
    ok: bool = Field(description="True if Ollama is reachable, the model is available and the folders are usable.")
    server_version: str = Field(description="Version of this MCP server.")
    ollama_url: str
    ollama_reachable: bool
    ollama_version: str | None
    model: str
    model_available: bool
    auth_configured: bool = Field(description="Whether a bearer token (OLLAMA_API_KEY) is set.")
    num_ctx: int
    context_budget_chars: int
    doc_root: str
    documents: int
    output_dir: str
    output_writable: bool
    cache_dir: str
    problems: list[str] = Field(description="Human-readable description of anything that is wrong.")


class MentionCount(BaseModel):
    name: str
    term: str
    count: int
    range_start: int
    range_end: int


class YesNoResult(BaseModel):
    name: str
    answer: Literal["yes", "no", "unclear"]
    confidence: Literal["low", "medium", "high"]
    processing: Processing


class ClassifyResult(BaseModel):
    name: str
    label: str
    processing: Processing


class FileResult(BaseModel):
    written_to: str = Field(description="Path of the markdown note for the user to read.")
    words: int
    processing: Processing


class AssessmentResult(BaseModel):
    name: str
    score: int
    min_score: int
    max_score: int
    written_to: str = Field(description="Path of the markdown note with the justification.")
    processing: Processing


class Reference(BaseModel):
    text: str = Field(description="The reference exactly as it appears in the document.")
    kind: ReferenceKind


class ReferencesResult(BaseModel):
    name: str
    references: list[Reference]
    web_links: list[str] = Field(description="All URLs in the scanned text, found by pattern matching.")
    scope: list[str] = Field(description="Which parts were scanned, e.g. the detected 'References' section.")
    ranges: list[CharRange]
    chunks: int
    dropped_unverified: int = Field(description="Model outputs discarded as invalid, overlapping or too short.")
    written_to: str | None = None
