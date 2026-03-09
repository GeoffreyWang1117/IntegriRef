from .base import ReferenceAdapter, Reference
from .bibtex import BibtexAdapter
from .pdf import PdfAdapter
from .docx_adapter import DocxAdapter
from .text import TextAdapter

ADAPTERS = {
    ".bib": BibtexAdapter,
    ".pdf": PdfAdapter,
    ".docx": DocxAdapter,
    ".txt": TextAdapter,
    ".djvu": TextAdapter,  # djvutxt → plain text pipeline
}

__all__ = ["ReferenceAdapter", "Reference", "ADAPTERS",
           "BibtexAdapter", "PdfAdapter", "DocxAdapter", "TextAdapter"]
