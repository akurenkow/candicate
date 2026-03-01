"""
Validation for semantic resume structure: hierarchy consistency and required fields.
"""

from .dto import CompanyExperience, Project, Resume


class ValidationError(Exception):
    """Raised when resume structure fails validation."""

    def __init__(self, message: str, path: str = ""):
        self.message = message
        self.path = path
        super().__init__(f"{path}: {message}" if path else message)


def validate_resume(resume: Resume) -> list[str]:
    """
    Validate hierarchy consistency and required metadata.
    Returns list of warning/error messages; empty if valid.
    """
    errors: list[str] = []

    # At least one section should have content
    has_general = bool(resume.general.content_blocks)
    has_companies = bool(resume.companies)
    if not has_general and not has_companies:
        errors.append("Resume has no general section and no company experiences")

    for i, company in enumerate(resume.companies):
        prefix = f"companies[{i}]"
        if not company.metadata.get("company_name") and company.metadata.get("fallback") != "true":
            errors.append(f"{prefix}: missing metadata.company_name")
        has_projects = bool(company.projects)
        has_descriptions = bool(company.experience_descriptions)
        if not has_projects and not has_descriptions:
            errors.append(
                f"{prefix}: company has no projects and no experience_descriptions"
            )
        for j, project in enumerate(company.projects):
            pprefix = f"{prefix}.projects[{j}]"
            if not project.metadata.get("project_name"):
                errors.append(f"{pprefix}: missing metadata.project_name")
            if not project.content_blocks and not has_descriptions and j == 0:
                pass
            # Empty project is allowed (tolerate missing nesting)

    return errors


def validate_resume_strict(resume: Resume) -> None:
    """
    Validate and raise ValidationError on first inconsistency.
    Use for fail-fast in pipeline.
    """
    errors = validate_resume(resume)
    if errors:
        raise ValidationError("; ".join(errors), "resume")
