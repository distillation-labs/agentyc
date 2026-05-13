# Pydantic v2 Patterns

Grounded in traverse's usage of Pydantic v2 across BrowserSession, watchdogs, and LLM views.

## What Good Looks Like

- `model_config = ConfigDict(extra='forbid', ...)` on every model — implicit extras are bugs.
- `arbitrary_types_allowed=True` only when the model holds non-serializable types (EventBus, CDPClient).
- `validate_assignment=False` and `revalidate_instances='never'` on service models that use `PrivateAttr`.
- `Annotated[type, AfterValidator(...)]` for field-level invariants — more composable than `@field_validator`.
- `PrivateAttr(default_factory=...)` for mutable runtime caches that must not serialize.
- `Field(default_factory=uuid7str)` for all ID fields — time-ordered and unambiguous.
- Models in `views.py`, logic in `service.py` — no exceptions.
- Protocols used as Pydantic fields implement `__get_pydantic_core_schema__` returning `any_schema()`.
- Modern type syntax: `str | None`, `list[str]`, `dict[str, Any]` — never `Optional`, `List`, `Dict`.

## What To Avoid

- `Optional[X]` (use `X | None`)
- `List[X]`, `Dict[K, V]` (use `list[X]`, `dict[K, V]`)
- `extra='allow'` on data models that touch external input
- `validate_assignment=True` on service models (resets `PrivateAttr` values)
- mixing service methods into `BaseModel` subclasses
- using bare `dict` kwargs where a typed model would enforce the contract
- `@field_validator` when `AfterValidator` would do the same job
