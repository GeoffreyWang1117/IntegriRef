"""Temporally held-out Platt calibration test.

R4 ask: confirm the ECE 0.36->0.003 Platt improvement isn't an in-distribution
overfit by training Platt on EARLY cited-paper years and testing on LATE years.

Pipeline:
  1. Load posteriors + binary labels from rv-1926 and ret-6391 bench JSONs.
  2. Join with year metadata from the source jsonl files (via id mapping).
  3. Split temporally at the median cited-paper year.
  4. Fit Platt on train (early years), apply to test (late years).
  5. Report ECE-raw, ECE-after-Platt(temporal), ECE-after-Platt(5-fold CV) for
     direct comparison.
"""
import json, sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from benchmarks.calibration import compute_ece, compute_brier, load_pairs, platt_recalibrate

RESULTS = Path(__file__).parent.parent / "paper" / "gem2026" / "bench_results"
HF = Path(__file__).parent / "data" / "hf_export"

POS = {"retracted", "hallucinated", "chimera"}
NEG = {"real", "real_control"}


def load_year_map(jsonl: Path) -> dict[str, int]:
    """Return id -> year (int, or 0 if missing) from a source jsonl."""
    out = {}
    if not jsonl.exists():
        return out
    for line in open(jsonl):
        c = json.loads(line)
        cid = str(c.get("id", ""))
        y = c.get("year") or c.get("publication_year") or ""
        try:
            y = int(str(y)[:4])
        except Exception:
            y = 0
        out[cid] = y
    return out


def load_pairs_with_year(bench_json: Path, year_map: dict[str, int]):
    """Extract (posterior, label, year) for each labelled case."""
    d = json.load(open(bench_json))
    probs, labels, years = [], [], []
    for c in d.get("results", []):
        cat = c.get("category", "")
        if cat in POS:
            lab = 1
        elif cat in NEG:
            lab = 0
        else:
            continue
        p = float(c.get("posterior", 0.0))
        cid = str(c.get("id", ""))
        y = year_map.get(cid, 0)
        # rv-1926 IDs are e.g. "real_001", "hallucinated_042", etc.
        if y == 0 and "_" in cid:
            y = year_map.get(cid.split("_", 1)[1], 0)
        probs.append(p)
        labels.append(lab)
        years.append(y)
    return np.array(probs), np.array(labels), np.array(years)


def main():
    rv = sorted(RESULTS.glob("bench_reference_verification_2026*.json"))
    rv = [f for f in rv if "crossref_only" not in f.name][-1]
    ret = sorted(RESULTS.glob("bench_retracted_papers_2026*.json"))
    ret = [f for f in ret if "crossref_only" not in f.name][-1]
    print(f"Loading rv:  {rv.name}")
    print(f"Loading ret: {ret.name}")

    rv_year = load_year_map(HF / "reference_verification.jsonl")
    ret_year = load_year_map(HF / "retracted_papers.jsonl")

    p1, l1, y1 = load_pairs_with_year(rv, rv_year)
    p2, l2, y2 = load_pairs_with_year(ret, ret_year)
    probs = np.concatenate([p1, p2])
    labels = np.concatenate([l1, l2])
    years = np.concatenate([y1, y2])

    print(f"\nTotal labelled cases: {len(probs)}")
    print(f"  Positives: {labels.sum()}   Negatives: {(1-labels).sum()}")
    yr_valid = years[years > 0]
    print(f"  Year coverage: {len(yr_valid)}/{len(years)} ({100*len(yr_valid)/len(years):.1f}%)")
    if len(yr_valid):
        print(f"  Year range: {yr_valid.min()}-{yr_valid.max()}, median={int(np.median(yr_valid))}")

    # Drop cases without year for the temporal split
    mask = years > 0
    probs_v, labels_v, years_v = probs[mask], labels[mask], years[mask]

    # Temporal split at median year
    split_year = int(np.median(years_v))
    train_mask = years_v <= split_year
    test_mask = years_v > split_year
    print(f"\nTemporal split: train year<={split_year}  (n={train_mask.sum()})  "
          f"test year>{split_year} (n={test_mask.sum()})")
    print(f"  Train labels: pos={labels_v[train_mask].sum()}  neg={(1-labels_v[train_mask]).sum()}")
    print(f"  Test  labels: pos={labels_v[test_mask].sum()}   neg={(1-labels_v[test_mask]).sum()}")

    # Raw ECE on test split
    raw_ece_test = compute_ece(probs_v[test_mask], labels_v[test_mask], n_bins=10)
    raw_brier_test = compute_brier(probs_v[test_mask], labels_v[test_mask])
    print(f"\n[Raw on test split]   ECE={raw_ece_test['ece']:.4f}  Brier={raw_brier_test:.4f}")

    # Fit Platt on train, predict on test
    from sklearn.linear_model import LogisticRegression
    eps = 1e-6
    logit_train = np.log(np.clip(probs_v[train_mask], eps, 1-eps) /
                          np.clip(1-probs_v[train_mask], eps, 1-eps))
    logit_test = np.log(np.clip(probs_v[test_mask], eps, 1-eps) /
                         np.clip(1-probs_v[test_mask], eps, 1-eps))
    lr = LogisticRegression(C=1.0, solver="lbfgs")
    lr.fit(logit_train.reshape(-1, 1), labels_v[train_mask])
    cal_test = lr.predict_proba(logit_test.reshape(-1, 1))[:, 1]

    temp_ece = compute_ece(cal_test, labels_v[test_mask], n_bins=10)
    temp_brier = compute_brier(cal_test, labels_v[test_mask])
    print(f"[Temporal Platt]      ECE={temp_ece['ece']:.4f}  Brier={temp_brier:.4f}")
    print(f"  Platt logit weight a = {lr.coef_[0,0]:+.4f}   bias b = {lr.intercept_[0]:+.4f}")

    # Reference: 5-fold CV Platt on the full pool (existing method)
    cal_cv_full = platt_recalibrate(probs, labels, cv_folds=5)
    cv_ece = compute_ece(cal_cv_full, labels, n_bins=10)
    cv_brier = compute_brier(cal_cv_full, labels)
    print(f"[5-fold CV Platt full] ECE={cv_ece['ece']:.4f}  Brier={cv_brier:.4f}  (n={len(probs)})")

    # Also: 5-fold CV Platt restricted to the temporal-test subset for direct comparison
    cal_cv_test = platt_recalibrate(probs_v[test_mask], labels_v[test_mask], cv_folds=5)
    cv_test_ece = compute_ece(cal_cv_test, labels_v[test_mask], n_bins=10)
    print(f"[5-fold CV Platt test-only] ECE={cv_test_ece['ece']:.4f}  (n={test_mask.sum()})")

    out = {
        "split_year": split_year,
        "n_train": int(train_mask.sum()),
        "n_test": int(test_mask.sum()),
        "raw_test_ece": raw_ece_test["ece"],
        "raw_test_brier": raw_brier_test,
        "temporal_platt_ece": temp_ece["ece"],
        "temporal_platt_brier": temp_brier,
        "platt_coef_a": float(lr.coef_[0,0]),
        "platt_coef_b": float(lr.intercept_[0]),
        "cv_platt_full_ece": cv_ece["ece"],
        "cv_platt_full_brier": cv_brier,
        "cv_platt_test_only_ece": cv_test_ece["ece"],
        "n_with_year": int(mask.sum()),
        "n_total": int(len(probs)),
    }
    import time
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS / f"calibration_temporal_{ts}.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nSaved {out_path}")


if __name__ == "__main__":
    main()
