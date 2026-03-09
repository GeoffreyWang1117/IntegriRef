"""Plain text / DjVu adapter.

For .txt files: parse directly.
For .djvu files: convert to text via djvutxt, then parse.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .base import Reference, ReferenceAdapter
from .pdf import _parse_references_from_text


class TextAdapter(ReferenceAdapter):

    @staticmethod
    def supported_extensions() -> set[str]:
        return {".txt", ".djvu"}

    def extract(self, file_path: Path) -> list[Reference]:
        suffix = file_path.suffix.lower()

        if suffix == ".djvu":
            text = self._djvu_to_text(file_path)
        else:
            text = file_path.read_text(encoding="utf-8", errors="replace")

        return _parse_references_from_text(text)

    @staticmethod
    def _djvu_to_text(file_path: Path) -> str:
        """Convert DjVu to plain text using djvutxt."""
        try:
            result = subprocess.run(
                ["djvutxt", str(file_path)],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return ""
