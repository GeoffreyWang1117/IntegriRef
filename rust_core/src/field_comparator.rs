//! Field comparison — title, author, year, venue matching.
//! Drop-in replacement for verification/field_comparator.py hot paths.

use pyo3::prelude::*;
use std::collections::HashSet;

use crate::text_utils::{latex_tokenize, strip_accents, strip_latex};

// ── Similarity functions ─────────────────────────────────────────────

fn jaccard_sim(a: &HashSet<String>, b: &HashSet<String>) -> f64 {
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    let inter = a.intersection(b).count() as f64;
    let union = a.union(b).count() as f64;
    inter / union
}

/// Token-set ratio: intersection tokens / min(|a|, |b|) then blend with Jaccard.
/// Approximates RapidFuzz token_set_ratio without the C dependency.
fn token_set_ratio_impl(a_tokens: &HashSet<String>, b_tokens: &HashSet<String>) -> f64 {
    if a_tokens.is_empty() || b_tokens.is_empty() {
        return 0.0;
    }
    let inter = a_tokens.intersection(b_tokens).count() as f64;
    let min_len = a_tokens.len().min(b_tokens.len()) as f64;
    let max_len = a_tokens.len().max(b_tokens.len()) as f64;
    // Weighted blend: token overlap ratio + Jaccard
    let overlap = inter / min_len;
    let jac = inter / (a_tokens.len() + b_tokens.len()) as f64 * 2.0; // Dice coefficient
    let penalty = min_len / max_len; // length balance penalty
    (overlap * 0.6 + jac * 0.4) * (0.7 + 0.3 * penalty)
}

// ── Python-exported functions ────────────────────────────────────────

/// Jaccard similarity on tokens after LaTeX stripping.
#[pyfunction]
pub fn jaccard_similarity(a: &str, b: &str) -> f64 {
    let ta = latex_tokenize(a);
    let tb = latex_tokenize(b);
    jaccard_sim(&ta, &tb)
}

/// Token-set ratio (0-1 scale). Replaces RapidFuzz dependency.
#[pyfunction]
pub fn token_set_ratio(a: &str, b: &str) -> f64 {
    let a_clean = strip_latex(a).to_lowercase();
    let b_clean = strip_latex(b).to_lowercase();
    let ta = crate::text_utils::tokenize_set(&a_clean);
    let tb = crate::text_utils::tokenize_set(&b_clean);
    token_set_ratio_impl(&ta, &tb)
}

/// Best available token similarity — max(token_set_ratio, jaccard).
#[pyfunction]
pub fn token_similarity(a: &str, b: &str) -> f64 {
    let a_clean = strip_latex(a).to_lowercase();
    let b_clean = strip_latex(b).to_lowercase();
    let ta = crate::text_utils::tokenize_set(&a_clean);
    let tb = crate::text_utils::tokenize_set(&b_clean);
    let tsr = token_set_ratio_impl(&ta, &tb);
    let jac = jaccard_sim(&ta, &tb);
    tsr.max(jac)
}

/// Jaro-Winkler similarity for author names (0-1).
#[pyfunction]
pub fn author_name_similarity(a: &str, b: &str) -> f64 {
    let a_clean = strip_accents(&strip_latex(a));
    let b_clean = strip_accents(&strip_latex(b));
    if a_clean == b_clean {
        return 1.0;
    }
    if a_clean.is_empty() || b_clean.is_empty() {
        return 0.0;
    }
    strsim::jaro_winkler(&a_clean, &b_clean)
}

// ── Field matching (returns (status, detail) tuples) ─────────────────

const TITLE_OK: f64 = 0.85;
const TITLE_WARN: f64 = 0.70;

/// Compare titles → ("OK"/"WARN"/"FAIL", detail).
#[pyfunction]
pub fn match_title(bib_title: &str, api_title: &str) -> (String, String) {
    let score = token_similarity(bib_title, api_title);
    if score >= TITLE_OK {
        ("OK".into(), format!("title match ({score:.2})"))
    } else if score >= TITLE_WARN {
        ("WARN".into(), format!("title partial match ({score:.2}): '{api_title}'"))
    } else {
        let bt: String = bib_title.chars().take(60).collect();
        let at: String = api_title.chars().take(60).collect();
        ("FAIL".into(), format!("title mismatch ({score:.2}): bib='{bt}' vs api='{at}'"))
    }
}

/// Compare authors → ("OK"/"WARN"/"FAIL", detail).
#[pyfunction]
pub fn match_authors(bib_authors: Vec<String>, api_authors: Vec<String>) -> (String, String) {
    if api_authors.is_empty() {
        return ("WARN".into(), "no authors from API".into());
    }
    if bib_authors.is_empty() {
        return ("WARN".into(), "no authors in reference".into());
    }

    let bib_surname = extract_surname(&bib_authors[0]);
    let api_surname = extract_surname(&api_authors[0]);

    if bib_surname.is_empty() || api_surname.is_empty() {
        return ("WARN".into(), "could not extract surnames".into());
    }

    let sim = author_name_similarity(&bib_surname, &api_surname);
    let surname_ok = sim >= 0.9;

    let bib_count = bib_authors.len();
    let api_count = api_authors.len();
    let has_others = bib_authors.iter().any(|a| {
        let lower = a.to_lowercase();
        lower.contains("others") || lower.contains("et al")
    });
    let count_ok = has_others || bib_count.abs_diff(api_count) <= 2;

    if surname_ok && count_ok {
        ("OK".into(), format!("first author '{bib_surname}' matches, count bib={bib_count} api={api_count}"))
    } else if surname_ok {
        ("WARN".into(), format!("first author matches but count differs: bib={bib_count} api={api_count}"))
    } else {
        ("FAIL".into(), format!("first author mismatch: bib='{bib_surname}' api='{api_surname}'"))
    }
}

/// Compare years → ("OK"/"WARN"/"FAIL", detail).
#[pyfunction]
pub fn match_year(bib_year: &str, api_year: &str) -> (String, String) {
    if api_year.is_empty() {
        return ("WARN".into(), "no year from API".into());
    }
    let by: i32 = match bib_year.parse() {
        Ok(v) => v,
        Err(_) => return ("WARN".into(), format!("unparseable years: bib={bib_year} api={api_year}")),
    };
    let ay: i32 = match api_year.parse() {
        Ok(v) => v,
        Err(_) => return ("WARN".into(), format!("unparseable years: bib={bib_year} api={api_year}")),
    };
    let diff = (by - ay).unsigned_abs();
    if diff == 0 {
        ("OK".into(), format!("year exact match ({by})"))
    } else if diff <= 1 {
        ("WARN".into(), format!("year off by 1: bib={by} api={ay}"))
    } else {
        ("FAIL".into(), format!("year mismatch: bib={by} api={ay}"))
    }
}

// ── Batch operations (amortize FFI overhead) ─────────────────────────

/// Batch token similarity: process N pairs in one FFI call.
/// Returns Vec of similarity scores.
#[pyfunction]
pub fn batch_token_similarity(pairs: Vec<(String, String)>) -> Vec<f64> {
    pairs
        .iter()
        .map(|(a, b)| token_similarity(a, b))
        .collect()
}

/// Batch author similarity: process N pairs in one FFI call.
#[pyfunction]
pub fn batch_author_similarity(pairs: Vec<(String, String)>) -> Vec<f64> {
    pairs
        .iter()
        .map(|(a, b)| author_name_similarity(a, b))
        .collect()
}

/// Aggregate match score from (status, detail) pairs → 0-100.
#[pyfunction]
pub fn compute_match_score(checks: Vec<(String, String)>) -> f64 {
    if checks.is_empty() {
        return 0.0;
    }
    let total: f64 = checks
        .iter()
        .map(|(status, _)| match status.as_str() {
            "OK" => 100.0,
            "WARN" => 60.0,
            _ => 0.0,
        })
        .sum();
    total / checks.len() as f64
}

// ── Helpers ──────────────────────────────────────────────────────────

fn extract_surname(name: &str) -> String {
    let clean = strip_latex(name);
    let trimmed = clean.trim();
    if trimmed.is_empty() {
        return String::new();
    }
    if trimmed.contains(',') {
        // "Last, First" → last word of the part before comma
        let part = trimmed.split(',').next().unwrap_or("");
        return part.split_whitespace().last().unwrap_or("").to_string();
    }
    // "First Middle Last" → "Last"
    trimmed.split_whitespace().last().unwrap_or("").to_string()
}
