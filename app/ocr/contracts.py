"""Values exchanged between the PDF service and the isolated CPU worker."""
from dataclasses import dataclass, field


class OCRError(ValueError):
    """An actionable error safe to display to the user."""


@dataclass(frozen=True)
class OCRLine:
    text: str
    bbox: tuple[float, float, float, float]
    polygon: tuple[float, ...]
    confidence: float
    page_index: int
    word_box: bool = False


@dataclass(frozen=True)
class OCROptions:
    skip_existing_text: bool = True
    password: str | None = field(default=None, repr=False)
    runtime_dir: str | None = None


@dataclass(frozen=True)
class OCRJobResult:
    output_path: str
    processed_pages: tuple[int, ...]
    skipped_pages: tuple[int, ...]
    failed_pages: tuple[tuple[int, str], ...]
    warnings: tuple[str, ...] = ()
