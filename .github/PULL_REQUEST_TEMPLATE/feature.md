## Summary
- **Phase / Component**: ``
- **Architecture / ADRs**: `ADR-001` / `ADR-002` / `ADR-003`

## Technical Changes
- 

## Governance & Security Checklist
- [ ] `ruff check .` passes with zero warnings/errors
- [ ] `python scripts/validate_metrics_schema.py` passes
- [ ] `python scripts/render_catalog.py --check` & `python scripts/render_progress.py --check` pass
- [ ] `pytest -m "not live"` passes with 100% synthetic/offline fixtures (zero PII or secrets)
- [ ] UTF-8 without BOM + LF line endings verified

## Suggested CHANGELOG Entry
```markdown
### Added
- feat(<scope>): <concise lowercase description>
```
