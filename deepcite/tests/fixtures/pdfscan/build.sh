#!/bin/sh
# Rebuild the committed fixture PDFs (tests read the PDFs; they never need TeX).
set -e
cd "$(dirname "$0")"
T=$(mktemp -d)
for f in authoryear numeric; do
  cp $f.tex refs.bib "$T"/
  (cd "$T" && pdflatex -interaction=nonstopmode $f >/dev/null && bibtex $f >/dev/null \
     && pdflatex -interaction=nonstopmode $f >/dev/null && pdflatex -interaction=nonstopmode $f >/dev/null)
  cp "$T/$f.pdf" .
done
rm -rf "$T"
