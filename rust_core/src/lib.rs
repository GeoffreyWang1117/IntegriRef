//! IntegriRef Rust core — high-performance hot paths for Python verification pipeline.
//!
//! Modules:
//!   - `field_comparator`: Title/author/venue similarity (Jaccard, Jaro-Winkler, token_set_ratio)
//!   - `tortured_phrases`: Aho-Corasick multi-pattern detection for 350+ tortured phrases
//!   - `text_utils`: LaTeX stripping, accent normalization, tokenization

use pyo3::prelude::*;

mod field_comparator;
mod tortured_phrases;
mod text_utils;

/// IntegriRef Rust core module — drop-in acceleration for Python hot paths.
#[pymodule]
fn integriref_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Text utilities
    m.add_function(wrap_pyfunction!(text_utils::strip_latex, m)?)?;
    m.add_function(wrap_pyfunction!(text_utils::strip_accents, m)?)?;
    m.add_function(wrap_pyfunction!(text_utils::tokenize, m)?)?;

    // Field comparator functions
    m.add_function(wrap_pyfunction!(field_comparator::jaccard_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(field_comparator::token_set_ratio, m)?)?;
    m.add_function(wrap_pyfunction!(field_comparator::token_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(field_comparator::author_name_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(field_comparator::match_title, m)?)?;
    m.add_function(wrap_pyfunction!(field_comparator::match_authors, m)?)?;
    m.add_function(wrap_pyfunction!(field_comparator::match_year, m)?)?;
    m.add_function(wrap_pyfunction!(field_comparator::compute_match_score, m)?)?;

    // Batch operations (amortize FFI overhead)
    m.add_function(wrap_pyfunction!(field_comparator::batch_token_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(field_comparator::batch_author_similarity, m)?)?;

    // Tortured phrase detector
    m.add_class::<tortured_phrases::RustTorturedPhraseDetector>()?;

    Ok(())
}
