//! Aho-Corasick tortured phrase detection — single-pass O(n + m) scanning.
//! Replaces Python's pyahocorasick/regex fallback with pure Rust.

use aho_corasick::{AhoCorasick, MatchKind};
use pyo3::prelude::*;
use std::collections::{HashMap, HashSet};

/// A single match result returned to Python.
#[derive(Clone)]
struct PhraseHit {
    tortured: String,
    standard: String,
    start: usize,
    end: usize,
    context: String,
}

/// High-performance tortured phrase detector using Aho-Corasick automaton.
///
/// Builds the automaton once at construction; each `scan()` call is a single
/// linear pass over the input text — O(text_len + num_matches).
#[pyclass]
pub struct RustTorturedPhraseDetector {
    automaton: AhoCorasick,
    /// phrase index → (tortured_lower, standard)
    entries: Vec<(String, String)>,
    /// Number of distinct phrases
    phrase_count: usize,
}

#[pymethods]
impl RustTorturedPhraseDetector {
    /// Create detector from a dict of {tortured_phrase: standard_term}.
    #[new]
    fn new(phrases: HashMap<String, String>) -> PyResult<Self> {
        let mut entries: Vec<(String, String)> = Vec::with_capacity(phrases.len());
        let mut patterns: Vec<String> = Vec::with_capacity(phrases.len());

        for (tortured, standard) in &phrases {
            let key = tortured.to_lowercase();
            if key != standard.to_lowercase() {
                patterns.push(key.clone());
                entries.push((key, standard.clone()));
            }
        }

        let automaton = AhoCorasick::builder()
            .match_kind(MatchKind::Standard)
            .ascii_case_insensitive(true)
            .build(&patterns)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let phrase_count = entries.len();
        Ok(Self {
            automaton,
            entries,
            phrase_count,
        })
    }

    /// Scan text → dict with keys: text_length, match_count, unique_phrases,
    /// tortured_score, is_suspicious, matches (list of dicts).
    fn scan(&self, text: &str) -> PyResult<PyObject> {
        Python::with_gil(|py| {
            let matches = self.scan_impl(text);
            let unique: HashSet<&str> = matches.iter().map(|m| m.tortured.as_str()).collect();
            let unique_count = unique.len();
            let match_count = matches.len();
            let score = compute_score(match_count, unique_count, text.len());

            let result = pyo3::types::PyDict::new(py);
            result.set_item("text_length", text.len())?;
            result.set_item("match_count", match_count)?;
            result.set_item("unique_phrases", unique_count)?;
            result.set_item("tortured_score", (score * 10.0).round() / 10.0)?;
            result.set_item("is_suspicious", score >= 40.0)?;

            let match_list = pyo3::types::PyList::empty(py);
            for m in &matches {
                let d = pyo3::types::PyDict::new(py);
                d.set_item("tortured", &m.tortured)?;
                d.set_item("standard", &m.standard)?;
                d.set_item("position", m.start)?;
                d.set_item("context", &m.context)?;
                match_list.append(d)?;
            }
            result.set_item("matches", match_list)?;

            Ok(result.into())
        })
    }

    /// Scan abstract (stricter scoring: 1.2x boost).
    fn scan_abstract(&self, abstract_text: &str) -> PyResult<PyObject> {
        Python::with_gil(|py| {
            let matches = self.scan_impl(abstract_text);
            let unique: HashSet<&str> = matches.iter().map(|m| m.tortured.as_str()).collect();
            let unique_count = unique.len();
            let match_count = matches.len();
            let mut score = compute_score(match_count, unique_count, abstract_text.len());

            if match_count > 0 {
                score = (score * 1.2).min(100.0);
            }

            let result = pyo3::types::PyDict::new(py);
            result.set_item("text_length", abstract_text.len())?;
            result.set_item("match_count", match_count)?;
            result.set_item("unique_phrases", unique_count)?;
            result.set_item("tortured_score", (score * 10.0).round() / 10.0)?;
            result.set_item("is_suspicious", score >= 40.0)?;

            let match_list = pyo3::types::PyList::empty(py);
            for m in &matches {
                let d = pyo3::types::PyDict::new(py);
                d.set_item("tortured", &m.tortured)?;
                d.set_item("standard", &m.standard)?;
                d.set_item("position", m.start)?;
                d.set_item("context", &m.context)?;
                match_list.append(d)?;
            }
            result.set_item("matches", match_list)?;

            Ok(result.into())
        })
    }

    /// Number of phrases in the dictionary.
    #[getter]
    fn phrase_count(&self) -> usize {
        self.phrase_count
    }
}

impl RustTorturedPhraseDetector {
    fn scan_impl(&self, text: &str) -> Vec<PhraseHit> {
        if text.is_empty() {
            return Vec::new();
        }

        let text_lower = text.to_lowercase();
        let text_bytes = text_lower.as_bytes();
        let text_len = text_bytes.len();

        // Collect raw matches with word-boundary filtering
        let mut raw_hits: Vec<(usize, usize, usize)> = Vec::new(); // (start, end, entry_idx)

        for mat in self.automaton.find_iter(&text_lower) {
            let start = mat.start();
            let end = mat.end();
            let idx = mat.pattern().as_usize();

            // Word-boundary check
            if start > 0 {
                let prev = text_bytes[start - 1];
                if prev.is_ascii_alphanumeric() || prev == b'_' {
                    continue;
                }
            }
            if end < text_len {
                let next = text_bytes[end];
                if next.is_ascii_alphanumeric() || next == b'_' {
                    continue;
                }
            }

            raw_hits.push((start, end, idx));
        }

        // Sort longest-first for overlap resolution
        raw_hits.sort_by(|a, b| (b.1 - b.0).cmp(&(a.1 - a.0)));

        // Remove overlapping matches (keep longest)
        let mut accepted: Vec<(usize, usize)> = Vec::new();
        let mut results: Vec<PhraseHit> = Vec::new();

        for (start, end, idx) in raw_hits {
            let overlaps = accepted.iter().any(|&(s, e)| start < e && end > s);
            if overlaps {
                continue;
            }
            accepted.push((start, end));

            let (ref tortured, ref standard) = self.entries[idx];
            let context = make_context(text, start, end, 40);
            results.push(PhraseHit {
                tortured: tortured.clone(),
                standard: standard.clone(),
                start,
                end,
                context,
            });
        }

        // Sort by position for output
        results.sort_by_key(|h| h.start);
        results
    }
}

fn make_context(text: &str, start: usize, end: usize, window: usize) -> String {
    let ctx_start = start.saturating_sub(window);
    let ctx_end = (end + window).min(text.len());

    // Be careful with UTF-8 boundaries
    let safe_start = if ctx_start == 0 {
        0
    } else {
        // Find valid UTF-8 boundary
        let mut s = ctx_start;
        while s > 0 && !text.is_char_boundary(s) {
            s -= 1;
        }
        s
    };
    let safe_end = {
        let mut e = ctx_end;
        while e < text.len() && !text.is_char_boundary(e) {
            e += 1;
        }
        e
    };

    let mut ctx = text[safe_start..safe_end].replace('\n', " ");
    ctx = ctx.trim().to_string();
    if safe_start > 0 {
        ctx = format!("...{ctx}");
    }
    if safe_end < text.len() {
        ctx = format!("{ctx}...");
    }
    ctx
}

fn compute_score(match_count: usize, unique_count: usize, _text_length: usize) -> f64 {
    if match_count == 0 {
        return 0.0;
    }

    let base = match match_count {
        1 => 30.0,
        2 => 55.0,
        3 => 70.0,
        4 => 80.0,
        5 => 90.0,
        n if n > 5 => 90.0 + ((n - 5) as f64 * 2.0).min(10.0),
        _ => 0.0,
    };

    let mut score = base;
    if match_count > 1 && unique_count > 1 {
        let diversity = unique_count as f64 / match_count as f64;
        score = (score + diversity * 3.0).min(100.0);
    }

    score.min(100.0)
}
