//! Text preprocessing — LaTeX stripping, accent removal, tokenization.
//! These are called thousands of times during batch verification.

use pyo3::prelude::*;
use std::collections::HashSet;
use unicode_normalization::UnicodeNormalization;

/// Strip LaTeX markup, returning plain Unicode text.
#[pyfunction]
pub fn strip_latex(s: &str) -> String {
    if s.is_empty() {
        return String::new();
    }

    let mut result = s.to_string();

    // \textbf{...}, \textit{...}, etc.
    let re_text_cmd =
        regex_lite::Regex::new(r"\\text\w+\{([^}]*)\}").unwrap();
    result = re_text_cmd.replace_all(&result, "$1").to_string();

    // Accent commands: \\'{e} → é, \\"{o} → ö, etc.
    // Handle both \'{x} and \'x forms
    let re_accent_brace =
        regex_lite::Regex::new(r#"\\([`'^~"=.vc])\{(\w)\}"#).unwrap();
    result = re_accent_brace
        .replace_all(&result, |caps: &regex_lite::Captures| {
            apply_accent(&caps[1], &caps[2])
        })
        .to_string();

    let re_accent_bare =
        regex_lite::Regex::new(r#"\\([`'^~"=.vc])(\w)"#).unwrap();
    result = re_accent_bare
        .replace_all(&result, |caps: &regex_lite::Captures| {
            apply_accent(&caps[1], &caps[2])
        })
        .to_string();

    // \$ → $, \& → &, etc.
    let re_escaped = regex_lite::Regex::new(r"\\(\W)").unwrap();
    result = re_escaped.replace_all(&result, "$1").to_string();

    // Remove remaining \commands
    let re_cmd = regex_lite::Regex::new(r"\\[a-zA-Z]+\s*").unwrap();
    result = re_cmd.replace_all(&result, "").to_string();

    // Strip braces and dollar signs
    result = result.replace(['{', '}', '$'], "");

    // Collapse whitespace
    let re_ws = regex_lite::Regex::new(r"\s+").unwrap();
    result = re_ws.replace_all(&result, " ").to_string();
    result.trim().to_string()
}

fn apply_accent(cmd: &str, ch: &str) -> String {
    let combining = match cmd {
        "`" => '\u{0300}',
        "'" => '\u{0301}',
        "^" => '\u{0302}',
        "~" => '\u{0303}',
        "=" => '\u{0304}',
        "." => '\u{0307}',
        "\"" => '\u{0308}',
        "v" => '\u{030C}',
        "c" => '\u{0327}',
        "u" => '\u{0306}',
        _ => return ch.to_string(),
    };
    let mut s = String::with_capacity(8);
    s.push_str(ch);
    s.push(combining);
    s.nfc().collect::<String>()
}

/// Strip all accents/diacritics and lowercase.
#[pyfunction]
pub fn strip_accents(s: &str) -> String {
    let nfkd: String = s.nfkd().collect();
    // Replace Turkish dotless i
    let nfkd = nfkd.replace('\u{0131}', "i");
    nfkd.chars()
        .filter(|c| !unicode_normalization::char::is_combining_mark(*c))
        .collect::<String>()
        .to_lowercase()
}

/// Tokenize into lowercase alphanumeric token set (returns sorted Vec for Python).
#[pyfunction]
pub fn tokenize(s: &str) -> Vec<String> {
    let set: HashSet<String> = s
        .to_lowercase()
        .split(|c: char| !c.is_alphanumeric())
        .filter(|t| !t.is_empty())
        .map(|t| t.to_string())
        .collect();
    let mut v: Vec<String> = set.into_iter().collect();
    v.sort();
    v
}

/// Internal: tokenize to HashSet (used by comparator functions).
pub(crate) fn tokenize_set(s: &str) -> HashSet<String> {
    s.to_lowercase()
        .split(|c: char| !c.is_alphanumeric())
        .filter(|t| !t.is_empty())
        .map(|t| t.to_string())
        .collect()
}

/// Internal: strip_latex then tokenize_set.
pub(crate) fn latex_tokenize(s: &str) -> HashSet<String> {
    tokenize_set(&strip_latex(s))
}
