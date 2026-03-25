# L0 Sync Strategy: IntegriRef ↔ bibguard

## Problem

bibguard (open-source L0) and IntegriRef (research L0) share core logic but
have different architectures. Improvements to either should flow to the other.

## Architecture Difference

```
bibguard (pip/npm)                IntegriRef verification/
─────────────────                 ────────────────────────
5 direct API calls                62 RegistryAdapter via RegistryDiscovery
VerificationResult (simple)       ReferenceResult (rich: hallucination, composite, provenance)
matching.py (Jaccard + optional   field_comparator.py (RapidFuzz + Rust accel)
  RapidFuzz)
core.py (277 lines)               engine.py (1048 lines)
                                  + hallucination_detector.py (318 lines)
                                  + tortured_phrases.py
                                  + email_risk.py
                                  + statistical.py (GRIM + statcheck)
                                  + sneaked_references.py
                                  + retraction_checker.py
                                  + metadata_validator.py
```

## What to Sync (manually, per release)

### bibguard → IntegriRef (matching logic improvements)

These improvements in bibguard should be ported to IntegriRef:

1. **Year tolerance** (v0.3.0): ±2 = WARN instead of FAIL
   - File: `field_comparator.py` → `match_year()`

2. **Venue abbreviation map** (v0.3.0): expanded from 14 to 39
   - File: `field_comparator.py` → `_VENUE_ABBREVS`, `_DBLP_VENUE_MAP`

3. **Type awareness** (v0.3.0): @misc/@online downgrade
   - File: `engine.py` → `verify_reference()`

4. **Confirmed-match year downgrade** (v0.3.0): off-by-1 → OK when title+author confirmed
   - File: `engine.py` → post-processing section

### IntegriRef → bibguard (detection capabilities)

These are candidates for future bibguard releases:

1. **Retraction checking** (dedicated `is_retracted` field)
   - Already partially in bibguard via Crossref `update-to`
   - Could add Retraction Watch API integration

2. **Hallucination scoring** (0-100 with 7 signal types)
   - bibguard has phantom_doi + kill-shot but no scoring model

3. **RapidFuzz** (already optional in bibguard via `[fast]`)
   - IntegriRef always uses it; bibguard falls back to Jaccard

## What NOT to Sync

- IntegriRef's 62-adapter architecture (bibguard uses 5 direct APIs by design)
- IntegriRef's Rust acceleration (bibguard targets simplicity)
- IntegriRef's HallucinationReport/CompositeScore/Provenance dataclasses
  (bibguard uses simple dicts for portability)
- IntegriRef's tortured_phrases/email_risk/GRIM/statcheck/sneaked_refs
  (these are L0+ research features, not core L0 existence checking)

## How to Sync

After each bibguard release:

```bash
# Check what changed in matching logic
diff ~/Engineering/ref-check/src/bibguard/matching.py \
     ~/Projects/IntegriRef/verification/field_comparator.py

# Check what changed in core verification logic
diff ~/Engineering/ref-check/src/bibguard/core.py \
     ~/Projects/IntegriRef/verification/engine.py
```

Port relevant changes manually — the architectures are too different for
automated sync, but the matching algorithms and heuristics should stay aligned.

## Future: Optional bibguard Dependency

If bibguard grows stable enough, IntegriRef could optionally use it:

```python
# In IntegriRef verification/engine.py (future)
try:
    from bibguard.matching import match_year, match_venue, _VENUE_ABBREVS
    # Use bibguard's continuously updated venue maps
except ImportError:
    # Fall back to local implementation
    from .field_comparator import match_year, match_venue
```

This would let IntegriRef automatically benefit from bibguard's venue map
expansions without manual porting.
