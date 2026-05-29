# Eval Rubric — Pydantic v2 Engineer

## Pass when the skill:

- loads for Pydantic v2 model design, ConfigDict choices, validators, views/service split, and schema generation
- does not load for CDP protocol work, async task management, or generic Python refactors
- specifies `ConfigDict` intentionally
- prefers `X | None`, `list[X]`, and `dict[K, V]` over legacy typing syntax
- recommends `PrivateAttr` for mutable runtime state
- recommends `Field(default_factory=uuid7str)` for ID fields where applicable
- prefers `AfterValidator` for field-level constraints and `@model_validator(mode='after')` for cross-field rules
- keeps models in `views.py` and service logic in `service.py`

## Fail when the skill:

- relies on implicit model defaults for important behavior
- mixes service logic into data models by default
- uses `Optional`, `List`, or `Dict` style guidance as the default recommendation
- recommends `validate_assignment=True` on service models that depend on private runtime state
- leaves schema and validation choices unexplained
