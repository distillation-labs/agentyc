from enum import Enum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from ._profile_validation import validate_cli_arg, validate_float_range, validate_url


class ViewportSize(BaseModel):
	width: int = Field(ge=0)
	height: int = Field(ge=0)

	def __getitem__(self, key: str) -> int:
		return dict(self)[key]

	def __setitem__(self, key: str, value: int) -> None:
		setattr(self, key, value)


class RecordHarContent(str, Enum):
	OMIT = 'omit'
	EMBED = 'embed'
	ATTACH = 'attach'


class RecordHarMode(str, Enum):
	FULL = 'full'
	MINIMAL = 'minimal'


class BrowserChannel(str, Enum):
	CHROMIUM = 'chromium'
	CHROME = 'chrome'
	CHROME_BETA = 'chrome-beta'
	CHROME_DEV = 'chrome-dev'
	CHROME_CANARY = 'chrome-canary'
	MSEDGE = 'msedge'
	MSEDGE_BETA = 'msedge-beta'
	MSEDGE_DEV = 'msedge-dev'
	MSEDGE_CANARY = 'msedge-canary'


UrlStr = Annotated[str, AfterValidator(validate_url)]
NonNegativeFloat = Annotated[float, AfterValidator(lambda x: validate_float_range(x, 0, float('inf')))]
CliArgStr = Annotated[str, AfterValidator(validate_cli_arg)]
