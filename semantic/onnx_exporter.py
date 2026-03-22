"""ONNX exporter & runtime — export DeBERTa/SciBERT to ONNX for 2-8x inference speedup.

Exports HuggingFace transformer models to ONNX format and provides
an optimized inference wrapper using ONNX Runtime.

Usage:
    # Export model to ONNX
    export_to_onnx("models/scifact_nli", "models/scifact_nli/model.onnx")

    # Use ONNX for fast inference
    verifier = ONNXNLIVerifier("models/scifact_nli/model.onnx",
                                "models/scifact_nli")
    result = verifier.verify("claim text", "abstract text")

Performance expectations:
    - CPU: 2-4x faster than PyTorch (FP32)
    - CPU + INT8 quantization: 4-8x faster
    - GPU + FP16: 2-3x faster than PyTorch GPU
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def export_to_onnx(model_path: str, output_path: str,
                   opset_version: int = 14,
                   optimize: bool = True,
                   quantize: bool = False) -> str:
    """Export a HuggingFace model to ONNX format.

    Args:
        model_path: Path to HuggingFace model directory or model name
        output_path: Path for the output .onnx file
        opset_version: ONNX opset version (14 works well for DeBERTa)
        optimize: Apply ONNX graph optimizations
        quantize: Apply dynamic INT8 quantization (CPU only, ~2x speedup)

    Returns:
        Path to the exported ONNX file.
    """
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    logger.info("Loading model from %s", model_path)
    tokenizer = AutoTokenizer.from_pretrained(model_path)
    model = AutoModelForSequenceClassification.from_pretrained(model_path)
    model.eval()

    # Create dummy input
    dummy = tokenizer(
        "This is a premise sentence.",
        "This is a hypothesis sentence.",
        return_tensors="pt",
        max_length=512,
        truncation=True,
        padding="max_length",
    )

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Export
    logger.info("Exporting to ONNX: %s", output_path)
    with torch.no_grad():
        torch.onnx.export(
            model,
            tuple(dummy.values()),
            output_path,
            opset_version=opset_version,
            input_names=["input_ids", "attention_mask", "token_type_ids"],
            output_names=["logits"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "sequence"},
                "attention_mask": {0: "batch", 1: "sequence"},
                "token_type_ids": {0: "batch", 1: "sequence"},
                "logits": {0: "batch"},
            },
        )

    file_size = os.path.getsize(output_path) / (1024 * 1024)
    logger.info("Exported ONNX model: %.1f MB", file_size)

    # Optimize
    if optimize:
        output_path = _optimize_onnx(output_path)

    # Quantize
    if quantize:
        output_path = _quantize_onnx(output_path)

    return output_path


def _optimize_onnx(model_path: str) -> str:
    """Apply ONNX graph optimizations."""
    try:
        from onnxruntime.transformers import optimizer
        optimized_path = model_path.replace(".onnx", "_opt.onnx")
        opt_model = optimizer.optimize_model(
            model_path,
            model_type="bert",
            num_heads=12,
            hidden_size=768,
            optimization_options=None,
        )
        opt_model.save_model_to_file(optimized_path)
        opt_size = os.path.getsize(optimized_path) / (1024 * 1024)
        logger.info("Optimized ONNX model: %.1f MB → %s", opt_size, optimized_path)
        return optimized_path
    except Exception as e:
        logger.warning("ONNX optimization failed, using unoptimized: %s", e)
        return model_path


def _quantize_onnx(model_path: str) -> str:
    """Apply dynamic INT8 quantization."""
    try:
        from onnxruntime.quantization import quantize_dynamic, QuantType
        quantized_path = model_path.replace(".onnx", "_int8.onnx")
        quantize_dynamic(
            model_path,
            quantized_path,
            weight_type=QuantType.QInt8,
        )
        q_size = os.path.getsize(quantized_path) / (1024 * 1024)
        logger.info("Quantized ONNX model: %.1f MB → %s", q_size, quantized_path)
        return quantized_path
    except Exception as e:
        logger.warning("ONNX quantization failed: %s", e)
        return model_path


class ONNXNLIVerifier:
    """ONNX Runtime-based NLI verifier — drop-in replacement for NLIVerifier.

    Uses ONNX Runtime for 2-8x faster inference vs PyTorch.
    Falls back to PyTorch NLIVerifier if ONNX Runtime unavailable.
    """

    def __init__(self, onnx_path: str = "", tokenizer_path: str = "",
                 device: str = "cpu", num_threads: int = 0):
        """
        Args:
            onnx_path: Path to ONNX model file
            tokenizer_path: Path to tokenizer (same as original HF model)
            device: "cpu" or "cuda"
            num_threads: CPU inference threads (0 = auto)
        """
        self._onnx_path = onnx_path
        self._tokenizer_path = tokenizer_path or os.path.dirname(onnx_path)
        self._device = device
        self._num_threads = num_threads
        self._session = None
        self._tokenizer = None
        self._loaded = False

    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return self._session is not None
        self._loaded = True

        if not self._onnx_path or not os.path.exists(self._onnx_path):
            logger.warning("ONNX model not found: %s", self._onnx_path)
            return False

        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer

            # Configure session options
            sess_options = ort.SessionOptions()
            sess_options.graph_optimization_level = (
                ort.GraphOptimizationLevel.ORT_ENABLE_ALL)
            if self._num_threads > 0:
                sess_options.intra_op_num_threads = self._num_threads
                sess_options.inter_op_num_threads = self._num_threads

            # Select execution provider
            if self._device == "cuda":
                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            else:
                providers = ["CPUExecutionProvider"]

            self._session = ort.InferenceSession(
                self._onnx_path,
                sess_options=sess_options,
                providers=providers,
            )
            self._tokenizer = AutoTokenizer.from_pretrained(self._tokenizer_path)

            provider = self._session.get_providers()[0]
            logger.info("ONNX Runtime loaded: %s (provider: %s)",
                        self._onnx_path, provider)
            return True

        except ImportError:
            logger.warning("onnxruntime not installed, falling back to PyTorch")
            return False
        except Exception as e:
            logger.error("Failed to load ONNX model: %s", e)
            return False

    def predict_batch(self, premises: list[str], hypotheses: list[str],
                      batch_size: int = 64) -> list[tuple[float, float, float]]:
        """Batched NLI prediction returning (contradiction, entailment, neutral) tuples.

        Compatible with NLIVerifier._batched_nli() interface.
        """
        if not self._ensure_loaded():
            raise RuntimeError("ONNX model not loaded")

        import numpy as np

        all_probs = []
        for i in range(0, len(premises), batch_size):
            batch_p = premises[i:i + batch_size]
            batch_h = hypotheses[i:i + batch_size]

            inputs = self._tokenizer(
                batch_p, batch_h,
                return_tensors="np",
                truncation=True,
                max_length=512,
                padding=True,
            )

            # Run inference — only pass inputs the model actually expects
            model_input_names = {inp.name for inp in self._session.get_inputs()}
            ort_inputs = {
                "input_ids": inputs["input_ids"].astype(np.int64),
                "attention_mask": inputs["attention_mask"].astype(np.int64),
            }
            if "token_type_ids" in model_input_names and "token_type_ids" in inputs:
                ort_inputs["token_type_ids"] = inputs["token_type_ids"].astype(np.int64)

            logits = self._session.run(["logits"], ort_inputs)[0]

            # Softmax
            exp_logits = np.exp(logits - np.max(logits, axis=-1, keepdims=True))
            probs = exp_logits / exp_logits.sum(axis=-1, keepdims=True)

            for p in probs:
                all_probs.append((float(p[0]), float(p[1]), float(p[2])))

        return all_probs

    def verify(self, claim: str, abstract: str):
        """Verify a claim against an abstract using ONNX inference.

        Returns an NLIResult (same interface as NLIVerifier.verify()).
        """
        from .nli_verifier import NLIVerifier, NLIResult, AlignmentLabel

        if not claim or not abstract:
            return NLIResult(
                label=AlignmentLabel.UNVERIFIABLE,
                confidence=0.0, entailment_score=0.0,
                neutral_score=0.0, contradiction_score=0.0,
                claim=claim, premise=abstract,
                model_name=f"onnx:{self._onnx_path}",
            )

        if not self._ensure_loaded():
            # Fallback to PyTorch
            fallback = NLIVerifier()
            return fallback.verify(claim, abstract)

        claim_t = claim[:500]
        sentences = NLIVerifier._split_sentences(abstract)
        if not sentences:
            sentences = [abstract[:1500]]

        probs = self.predict_batch(sentences, [claim_t] * len(sentences))

        best_ent, best_con = 0.0, 0.0
        for con_s, ent_s, neu_s in probs:
            best_ent = max(best_ent, ent_s)
            best_con = max(best_con, con_s)

        ent = best_ent
        con = best_con
        neu = max(0.0, 1.0 - ent - con)

        label, confidence, needs_upgrade = NLIVerifier._classify(ent, neu, con)
        return NLIResult(
            label=label, confidence=confidence,
            entailment_score=ent, neutral_score=neu,
            contradiction_score=con,
            claim=claim_t, premise=abstract[:1500],
            model_name=f"onnx:{os.path.basename(self._onnx_path)}",
            needs_llm_upgrade=needs_upgrade,
        )

    def verify_batch(self, pairs: list[tuple[str, str]],
                     batch_size: int = 64) -> list:
        """Batch verify multiple (claim, abstract) pairs.

        Returns list of NLIResult objects.
        """
        from .nli_verifier import NLIVerifier, NLIResult, AlignmentLabel

        if not self._ensure_loaded():
            fallback = NLIVerifier()
            return fallback.verify_batch(pairs, batch_size=batch_size)

        # Collect all sentence-claim pairs
        all_premises = []
        all_hypotheses = []
        pair_info = []  # (idx, num_sentences, claim, abstract)

        for idx, (claim, abstract) in enumerate(pairs):
            if not claim or not abstract:
                pair_info.append((idx, 0, claim or "", abstract or ""))
                continue
            claim_t = claim[:500]
            sentences = NLIVerifier._split_sentences(abstract)
            if not sentences:
                pair_info.append((idx, 0, claim_t, abstract))
                continue
            pair_info.append((idx, len(sentences), claim_t, abstract))
            for sent in sentences:
                all_premises.append(sent)
                all_hypotheses.append(claim_t)

        # Run batched inference
        if all_premises:
            all_probs = self.predict_batch(
                all_premises, all_hypotheses, batch_size=batch_size)
        else:
            all_probs = []

        # Reassemble results
        results = []
        prob_offset = 0
        for idx, num_sents, claim_t, abstract in pair_info:
            if num_sents == 0:
                results.append(NLIResult(
                    label=AlignmentLabel.UNVERIFIABLE,
                    confidence=0.0, entailment_score=0.0,
                    neutral_score=0.0, contradiction_score=0.0,
                    claim=claim_t, premise=abstract,
                    model_name=f"onnx:{os.path.basename(self._onnx_path)}",
                ))
                continue

            best_ent, best_con = 0.0, 0.0
            for i in range(num_sents):
                con_s, ent_s, neu_s = all_probs[prob_offset + i]
                best_ent = max(best_ent, ent_s)
                best_con = max(best_con, con_s)
            prob_offset += num_sents

            ent = best_ent
            con = best_con
            neu = max(0.0, 1.0 - ent - con)
            label, confidence, needs_upgrade = NLIVerifier._classify(ent, neu, con)
            results.append(NLIResult(
                label=label, confidence=confidence,
                entailment_score=ent, neutral_score=neu,
                contradiction_score=con,
                claim=claim_t, premise=abstract[:1500],
                model_name=f"onnx:{os.path.basename(self._onnx_path)}",
                needs_llm_upgrade=needs_upgrade,
            ))

        return results
