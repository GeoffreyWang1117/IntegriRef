"""Compute ablation table from cached benchmark results.

Variants (computable from per-record signals_fired + posterior):
  A. DOI-existence only: fire if phantom_doi or reference_not_found
  B. L0 - chimera signal: any L0 except chimera_detected
  C. L0 multi-registry: any of 6 L0 signals
  D. L0 + L4 hand-set (current): cached posterior >= 0.05 (ELEVATED+)
  E. L0 + L4 + Platt: 5-fold CV Platt on cached posteriors, threshold at 0.05
"""
import json
import sys
from pathlib import Path
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import KFold

L0_SIGNALS = {
    "reference_not_found", "phantom_doi", "metadata_mismatch",
    "no_id_match", "chimera_detected", "retracted_citation",
}
DOI_EXIST = {"reference_not_found", "phantom_doi"}
L0_MINUS_CHIMERA = L0_SIGNALS - {"chimera_detected"}


def platt_rescale(posteriors, labels, n_folds=5):
    posteriors = np.asarray(posteriors, dtype=float)
    labels = np.asarray(labels, dtype=int)
    eps = 1e-6
    logits = np.log(np.clip(posteriors, eps, 1 - eps) /
                    np.clip(1 - posteriors, eps, 1 - eps)).reshape(-1, 1)
    out = np.zeros_like(posteriors)
    for tr, te in KFold(n_splits=n_folds, shuffle=True, random_state=42).split(logits):
        clf = LogisticRegression()
        clf.fit(logits[tr], labels[tr])
        out[te] = clf.predict_proba(logits[te])[:, 1]
    return out


def fmt(v):
    return "---" if v is None else f"{100*v:.1f}"


def metric(records, variant_fn):
    if not records:
        return None
    return sum(variant_fn(r) for r in records) / len(records)


def main():
    base = Path("paper/gem2026/bench_results")
    rv = json.loads((base / "bench_reference_verification_20260524_215031.json").read_text())
    ret = json.loads((base / "bench_retracted_papers_20260524_202842.json").read_text())

    # Compute Platt on pooled data
    pool_idx_rv, pool_idx_ret = [], []
    posts, labels = [], []
    for i, r in enumerate(rv["results"]):
        if r["category"] in {"hallucinated", "chimera", "retracted"}:
            posts.append(r["posterior"]); labels.append(1); pool_idx_rv.append(i)
        elif r["category"] == "real":
            posts.append(r["posterior"]); labels.append(0); pool_idx_rv.append(i)
    for i, r in enumerate(ret["results"]):
        if r["category"] == "retracted":
            posts.append(r["posterior"]); labels.append(1); pool_idx_ret.append(i)
        elif r["category"] == "real_control":
            posts.append(r["posterior"]); labels.append(0); pool_idx_ret.append(i)

    print(f"Pool: {len(labels)} ({sum(labels)} pos, {len(labels) - sum(labels)} neg)", file=sys.stderr)
    platt_out = platt_rescale(posts, labels)

    # Map platt back
    k = 0
    for i in pool_idx_rv:
        rv["results"][i]["_platt"] = float(platt_out[k]); k += 1
    for i in pool_idx_ret:
        ret["results"][i]["_platt"] = float(platt_out[k]); k += 1

    variants = {
        "DOI-existence":   lambda r: any(s in DOI_EXIST for s in r["signals_fired"]),
        "L0 - chimera":    lambda r: any(s in L0_MINUS_CHIMERA for s in r["signals_fired"]),
        "L0 multi-reg":    lambda r: any(s in L0_SIGNALS for s in r["signals_fired"]),
        "L0+L4 hand":      lambda r: r["posterior"] >= 0.05,
        "L0+L4 Platt":     lambda r: r.get("_platt", r["posterior"]) >= 0.05,
    }

    rv_results = rv["results"]
    cats_rv = {
        "halluc": [r for r in rv_results if r["category"] == "hallucinated"],
        "chimera": [r for r in rv_results if r["category"] == "chimera"],
        "retr": [r for r in rv_results if r["category"] == "retracted"],
        "real": [r for r in rv_results if r["category"] == "real"],
    }
    print(f"\nrv-1926: halluc={len(cats_rv['halluc'])} chim={len(cats_rv['chimera'])} retr={len(cats_rv['retr'])} real={len(cats_rv['real'])}")
    print(f"{'Variant':<16} {'HallucR':>9} {'ChimR':>9} {'RetrR':>9} {'FPR':>9}")
    rv_rows = {}
    for name, vfn in variants.items():
        h = metric(cats_rv['halluc'], vfn)
        c = metric(cats_rv['chimera'], vfn)
        r_ = metric(cats_rv['retr'], vfn)
        f = metric(cats_rv['real'], vfn)
        rv_rows[name] = (h, c, r_, f)
        print(f"{name:<16} {fmt(h):>9} {fmt(c):>9} {fmt(r_):>9} {fmt(f):>9}")

    cats_ret = {
        "retr": [r for r in ret["results"] if r["category"] == "retracted"],
        "real": [r for r in ret["results"] if r["category"] == "real_control"],
    }
    print(f"\nret-6391: retr={len(cats_ret['retr'])} real_control={len(cats_ret['real'])}")
    print(f"{'Variant':<16} {'RetrR':>9} {'FPR':>9}")
    ret_rows = {}
    for name, vfn in variants.items():
        r_ = metric(cats_ret['retr'], vfn)
        f = metric(cats_ret['real'], vfn)
        ret_rows[name] = (None, None, r_, f)
        print(f"{name:<16} {fmt(r_):>9} {fmt(f):>9}")

    out_path = base / "ablation_results.json"
    out_path.write_text(json.dumps({"rv_1926": rv_rows, "ret_6391": ret_rows}, indent=2))
    print(f"\nSaved {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
