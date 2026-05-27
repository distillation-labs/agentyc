---
name: pydantic-v2-engineer
description: >
  Use for designing Pydantic v2 models in agentyc: ConfigDict tuning, validators, model
  splitting across service.py and views.py, PrivateAttr usage, Protocol integration, and
  schema generation for MCP tool surfaces. Trigger when the user asks how to structure a data
  model, how to add runtime validation, how to use AfterValidator, when to use PrivateAttr vs
  Field, or how to make a Pydantic model work with arbitrary types like CDPClient or EventBus.
  Do not use for general Python dataclass or TypedDict design.
when_to_use: >
  Especially useful for ConfigDict choices, validator composition, service/views file split,
  cross-model constraints, Protocol-as-field patterns, and schema export for MCP tool parameters.
metadata:
  version: "0.2.0"
  category: pydantic
  tags: [pydantic, pydantic-v2, models, validation, configdict, validators, views, service, schema]
license: Proprietary
---

# Pydantic v2 Engineer

Model the contract before writing the logic. Put data shapes in `views.py`, keep services
in `service.py`, and encode invariants as validators rather than helper methods.

## Core Rules

- Put pydantic models in `views.py`, service logic in `service.py`. Never mix them unless the
  model is trivially small and private to one file.
- Use `model_config = ConfigDict(...)` on every model — never rely on implicit defaults.
- Prefer `Annotated[type, AfterValidator(...)]` over `@field_validator` for field-level constraints.
- Use `PrivateAttr` for mutable runtime state that must not be serialized or validated.
- Use `Field(default_factory=uuid7str)` for all ID fields.
- Never use `Optional[X]` — use `X | None` and set a default where appropriate.
- Extend `views.py` / `service.py` into smaller typed modules when the surface grows: split views, validators, aliases/types, and builders rather than creating mega model files.
- Keep files between 300-500 lines max. Files above 500 lines must be split up — this is a strict rule, no exceptions.

## ConfigDict Patterns

| Scenario | ConfigDict settings |
|---|---|
| External API request/response | `extra='forbid', validate_by_name=True, validate_by_alias=True, populate_by_name=True` |
| Internal service model (mutable) | `arbitrary_types_allowed=True, extra='forbid', validate_assignment=False, revalidate_instances='never'` |
| Strict DTO / value object | `extra='forbid', frozen=True` |
| Protocol type in a field | `arbitrary_types_allowed=True` (required for Protocol fields like `BaseChatModel`) |

### v2.4+ additions
- `json_schema_serialization_defaults_required=True` — fields with defaults appear as required in JSON Schema.

### v2.7+ additions
- `use_attribute_docstrings=True` — use field docstrings in JSON Schema descriptions.
- `cache_strings=True` — enable string interning for memory efficiency on large models (default `True`).

### v2.11+ additions
- `validate_by_name=True` — accept field names in validation even when aliases exist.
- `validate_by_alias=True` — accept aliases in validation.
- `serialize_by_alias=True` — use aliases in serialization.

### v2.12+ additions
- `url_preserve_empty_path=True` — preserve empty path segments in URLs.
- `ser_json_temporal='iso8601'` — serialize temporal types as ISO 8601 strings.

### Watchdog / service model pattern (from BaseWatchdog)
```python
model_config = ConfigDict(
    arbitrary_types_allowed=True,  # EventBus, CDPClient, etc.
    extra='forbid',                # no implicit state
    validate_assignment=False,     # avoid re-triggering validators on assignment
    revalidate_instances='never',  # avoid erasing PrivateAttr values
)
```

## Validators

### AfterValidator (preferred for field-level)
```python
from typing import Annotated
from pydantic import AfterValidator

def _must_be_https(v: str) -> str:
    assert v.startswith('https://'), 'URL must be HTTPS'
    return v

SecureUrl = Annotated[str, AfterValidator(_must_be_https)]

class Config(BaseModel):
    endpoint: SecureUrl
```

### @model_validator for cross-field constraints
```python
from pydantic import model_validator

class BrowserProfile(BaseModel):
    headless: bool = False
    display: str | None = None

    @model_validator(mode='after')
    def _validate_display(self) -> 'BrowserProfile':
        if self.headless and self.display is not None:
            raise ValueError('headless mode does not use a display')
        return self
```

Use `@field_validator` only when `AfterValidator` cannot express the constraint (e.g., when
you need access to field info or inter-field context).

## PrivateAttr vs Field

| Use | Annotation |
|---|---|
| Injected dependency (EventBus, CDPClient) | `Field()` with `arbitrary_types_allowed=True` |
| Mutable runtime cache / internal state | `PrivateAttr(default=None)` |
| Computed on first access | `@cached_property` (not Pydantic, plain Python) |

```python
class DomService(BaseModel):
    browser_session: BrowserSession = Field()        # injected, validated
    _cache: dict[str, Any] = PrivateAttr(default_factory=dict)  # mutable, not serialized
```

## Protocol as Pydantic Field

`BaseChatModel` is a `@runtime_checkable Protocol`. To use it as a Pydantic field type:
- The Protocol must implement `__get_pydantic_core_schema__` returning `core_schema.any_schema()`.
- The hosting model must set `arbitrary_types_allowed=True`.

```python
from agentyc.llm.base import BaseChatModel

class AgentSettings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    llm: BaseChatModel
```

## ID Fields

```python
from uuid_extensions import uuid7str
from pydantic import Field

class Session(BaseModel):
    id: str = Field(default_factory=uuid7str)
```

Always use `uuid7str` (time-ordered UUIDs) for new IDs — not `uuid4`.

## Views / Service File Split

- `views.py`: all `BaseModel` subclasses, enums, type aliases, and constants.
- `service.py`: class with methods that operate on views. May import from `views.py` but not reverse.
- Test both layers independently: unit-test models in isolation, integration-test services with real dependencies.
- When `views.py` gets crowded, split along domain boundaries into focused modules such as `validators.py`, `types.py`, `aliases.py`, `builders.py`, or feature-specific `views_*.py` files.
- Prefer reusable validators and aliases over redeclaring the same constraints across multiple model files.

## Output Format

Return:
1. model layout (which fields, ConfigDict choices)
2. validator strategy (AfterValidator vs field_validator vs model_validator)
3. PrivateAttr vs Field decision
4. views/service file placement
5. schema export implications (for MCP tool parameters)
6. rejected alternatives

## Anti-Patterns

- `Optional[X]` — use `X | None`
- `List[X]`, `Dict[K, V]` — use `list[X]`, `dict[K, V]`
- implicit `extra='allow'` (undeclared fields slip through silently)
- `validate_assignment=True` on service models with PrivateAttr (resets private state)
- mixing service logic into `BaseModel` subclasses
- using `dict` kwargs where a typed model would make the contract explicit
- growing mega model files that mix unrelated views, validators, aliases, and builders in one place

## References

- `references/pydantic-patterns.md`
- `references/eval-rubric.md`
