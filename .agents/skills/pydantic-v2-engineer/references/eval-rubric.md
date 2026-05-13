# Eval Rubric — Pydantic v2 Engineer

## Triggering (routing)
- Skill loads for Pydantic v2 model design, ConfigDict choices, validators, views/service split, and schema generation.
- Skill does not load for CDP protocol work, async task management, or generic Python refactors.

## Model Design
- `ConfigDict` is always specified with at least `extra=` and relevant options.
- `X | None` is used instead of `Optional[X]`.
- `list[X]` / `dict[K, V]` used instead of `List[X]` / `Dict[K, V]`.
- `PrivateAttr` is recommended for mutable runtime state.
- `Field(default_factory=uuid7str)` is recommended for ID fields.

## Validator Strategy
- `AfterValidator` is preferred for field-level constraints.
- `@model_validator(mode='after')` is used for cross-field constraints.
- `@field_validator` is only recommended when the others can't express the constraint.

## File Organization
- Data models go in `views.py`, service logic in `service.py`.

## Output Quality
- Response includes ConfigDict choice with rationale.
- Anti-patterns (Optional, Dict, validate_assignment issues) are flagged.
