# Semantic Resume Parser

Parses resume text into a hierarchical structure for downstream embedding. **No document-language or format hardcoding**: delimiter and metadata are derived from structure only.

## Rules

- **Delimiter**: First line that contains **at least 5 consecutive dashes** (e.g. `-----` or `-----Section name-----`). Everything above = general; everything below = experience.
- **General section**: Paragraphs (split by `\n\n`) above the delimiter.
- **Experience section**: Hierarchy by leading dashes only:
  - **Level 1** (`-`): Company. Line content = `company_name`.
  - **First level-2** (`--`) under a company: line content = `date_range` (work period).
  - **Each further level-2** block: first line = `project_name`, rest = project content.
  - **Level 3+** (`---`, `----`): Content under current project or company.

Metadata keys: `company_name`, `date_range`, `project_name` (empty when not applicable).

**Label-aware extraction**: When a line matches `Period:`, `Company:`, `Project:` (or RU: `Период`, `Компания`, `Проект`), the value is assigned to the correct metadata field regardless of hierarchy. Lines matching `Position:`, `Technologies Used:`, `Responsibilities:`, `Achievements:` etc. are treated as content, not metadata.

**Smart heuristics**: When first level-2 has no label, parser checks if content looks like a date range (contains year, month names, dashes). If not date-like, it's assigned to `company_name` instead of `date_range`.

Embedding is done on the **translated** document; the parser is language-agnostic.

## Usage

```python
from semantic_resume import (
    build_resume_tree,
    validate_resume,
    validate_resume_strict,
    chunks_from_semantic_resume,
    Resume,
)

resume = build_resume_tree(translated_content)
errors = validate_resume(resume)
chunks_with_meta = chunks_from_semantic_resume(resume, include_metadata=True)
# List of (text, metadata); metadata: section_kind, company_name, date_range, project_name
```

## Pipeline

After `translate_ru_to_en_async`, call `build_resume_tree(content)` (no delimiter argument). Then `chunks_from_semantic_resume(resume)` for chunk texts and metadata.
