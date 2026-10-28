from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from app.auth.demo_login import demo_login_enabled
from app.web.i18n import get_template_gettext, get_template_ngettext


OSLO_TIMEZONE = ZoneInfo("Europe/Oslo")


def format_datetime_oslo(value: datetime | None) -> str:
    """Format an instant for Norwegian admin-facing pages."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(OSLO_TIMEZONE).strftime("%d.%m.%Y %H:%M")


STATIC_ROOT = Path("app/static")


@lru_cache(maxsize=256)
def _static_asset_version(path: str) -> str | None:
    try:
        return sha256((STATIC_ROOT / path).read_bytes()).hexdigest()[:12]
    except OSError:
        return None


@pass_context
def asset_url(context, path: str) -> str:
    """Static URL with a content-hash query, cached as immutable on Vercel."""
    url = str(context["request"].url_for("static", path=path))
    version = _static_asset_version(path)
    return f"{url}?v={version}" if version else url


@pass_context
def demo_login_on(context) -> bool:
    """True on a hosted demo with demo login (fake data only): shows the banner."""
    container = getattr(context["request"].app.state, "container", None)
    return container is not None and demo_login_enabled(container.settings)


templates = Jinja2Templates(directory="app/templates")
templates.env.add_extension("jinja2.ext.i18n")
templates.env.install_gettext_callables(
    gettext=get_template_gettext,
    ngettext=get_template_ngettext,
    newstyle=True,
)
templates.env.globals["format_datetime_oslo"] = format_datetime_oslo
templates.env.globals["asset_url"] = asset_url
templates.env.globals["demo_login_on"] = demo_login_on
