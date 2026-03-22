"""A-tier converter: SciFact dataset to IntegriRef L2 NLI format.

Converts the expert-annotated SciFact dataset (1,409 claim-abstract pairs)
into IntegriRef's L2 NLI schema. No API calls needed.

Usage:
    python -m benchmarks.crawlers scifact
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .base import BaseCrawler

logger = logging.getLogger(__name__)

# SciFact label → IntegriRef mapping
_LABEL_MAP = {
    "SUPPORT": ("entailment", "none"),
    "SUPPORTS": ("entailment", "none"),
    "CONTRADICT": ("contradiction", "claim_contradicted"),
    "REFUTES": ("contradiction", "claim_contradicted"),
    "NOT_ENOUGH_INFO": ("neutral", "claim_unsupported"),
}


class SciFActConverter(BaseCrawler):
    """Convert SciFact dataset to IntegriRef L2 NLI format."""

    name = "scifact_l2"

    def crawl(self, count: int = 0, scifact_dir: str = "", **kwargs) -> int:
        """Convert SciFact to L2 NLI format.

        Args:
            count: Max records to convert. 0 = all.
            scifact_dir: Path to SciFact data directory.
        """
        if scifact_dir:
            base = Path(scifact_dir)
        else:
            base = self.output_dir.parent / "scifact"

        if not base.exists():
            logger.error(
                "SciFact data not found at %s. Download from: "
                "https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz",
                base,
            )
            return 0

        # Load corpus
        corpus = self._load_corpus(base / "corpus.jsonl")
        if not corpus:
            logger.error("No corpus found in %s", base)
            return 0

        logger.info("Loaded SciFact corpus: %d documents", len(corpus))

        # Load and convert claims
        results = []
        for split in ["claims_train.jsonl", "claims_dev.jsonl", "claims_test.jsonl"]:
            fpath = base / split
            if not fpath.exists():
                continue

            with open(fpath, encoding="utf-8") as f:
                for line in f:
                    try:
                        claim = json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue

                    converted = self._convert_claim(claim, corpus, len(results), split)
                    if converted:
                        results.extend(converted)

                    if count > 0 and len(results) >= count:
                        break

            if count > 0 and len(results) >= count:
                break

        results = results[:count] if count > 0 else results

        out_path = self.output_dir / "scifact_l2_nli.jsonl"
        self._write_jsonl(results, out_path)

        # Category breakdown
        cats = {}
        for r in results:
            cats[r["category"]] = cats.get(r["category"], 0) + 1
        logger.info("SciFActConverter: %d cases %s", len(results), cats)
        return len(results)

    @staticmethod
    def _load_corpus(path: Path) -> dict[int, dict]:
        """Load SciFact corpus (doc_id → {title, abstract})."""
        corpus = {}
        if not path.exists():
            return corpus

        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    doc = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue

                doc_id = doc.get("doc_id", doc.get("id"))
                if doc_id is not None:
                    abstract_sents = doc.get("abstract", [])
                    corpus[int(doc_id)] = {
                        "title": doc.get("title", ""),
                        "abstract": " ".join(abstract_sents) if isinstance(abstract_sents, list)
                                    else str(abstract_sents),
                        "abstract_sentences": abstract_sents,
                    }

        return corpus

    def _convert_claim(self, claim: dict, corpus: dict,
                       base_idx: int, split: str) -> list[dict]:
        """Convert a single SciFact claim to L2 NLI records."""
        claim_text = claim.get("claim", "")
        if not claim_text:
            return []

        evidence = claim.get("evidence", {})
        results = []

        if not evidence:
            # No evidence → NOT_ENOUGH_INFO
            idx = base_idx + len(results)
            results.append({
                "id": f"scifact_{idx:04d}",
                "split": "l2_nli",
                "category": "neutral",
                "signal": "claim_unsupported",
                "claim": claim_text,
                "source_text": "",
                "expected_relation": "neutral",
                "domain": "biomedical",
                "note": f"SciFact {split}, no evidence",
            })
            return results

        for doc_id_str, rationales in evidence.items():
            doc_id = int(doc_id_str)
            doc = corpus.get(doc_id)
            if not doc:
                continue

            for rat in rationales:
                label = rat.get("label", "NOT_ENOUGH_INFO")
                sent_ids = rat.get("sentences", [])

                # Get evidence sentences
                abstract_sents = doc.get("abstract_sentences", [])
                evidence_text = " ".join(
                    abstract_sents[sid]
                    for sid in sent_ids
                    if sid < len(abstract_sents)
                )

                if not evidence_text:
                    evidence_text = doc.get("abstract", "")

                category, signal = _LABEL_MAP.get(
                    label, ("neutral", "claim_unsupported"))

                idx = base_idx + len(results)
                results.append({
                    "id": f"scifact_{idx:04d}",
                    "split": "l2_nli",
                    "category": category,
                    "signal": signal,
                    "claim": claim_text,
                    "source_text": evidence_text[:1000],
                    "expected_relation": category,
                    "domain": "biomedical",
                    "note": f"SciFact {split}, doc_id={doc_id}, "
                            f"evidence_sentences={sent_ids}",
                })

        return results
