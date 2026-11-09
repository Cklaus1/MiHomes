"""AI configuration helpers — API key resolution, model selection."""

import os

from sqlalchemy.orm import Session

from mihomes.services.ai.provider import AIAuthError
from mihomes.services.config_service import get_config

# Single source of truth for the default Claude model (spec H9/Q1). The prior
# default `claude-sonnet-4-20250514` reached its announced retirement on
# 2026-06-15, so fresh installs' AI calls 404'd. `claude-sonnet-5` is the
# current-generation balanced model and carries no dated-pin retirement trap.
DEFAULT_MODEL = "claude-sonnet-5"


def get_ai_api_key(session: Session, provider: str = "nim") -> str:
    """Resolve API key from env then config DB. Raises AIAuthError if not found."""
    env_vars = {
        "claude": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "nim": "NVIDIA_API_KEY",
    }

    # Try env var first
    env_var = env_vars.get(provider, f"{provider.upper()}_API_KEY")
    key = os.environ.get(env_var)
    if key:
        return key

    # Try config DB
    config_key = f"ai.{provider}_api_key"
    key = get_config(session, config_key)
    if key:
        return key

    raise AIAuthError(
        f"API key for {provider} not found. "
        f"Set {env_var} environment variable or run: mihomes ai setup"
    )


def get_ai_provider_name(session: Session) -> str:
    """Get configured AI provider name."""
    return get_config(session, "ai.provider", "nim")


#: `ai_priority` (the plan table, `entitlements/limits.py`) → the Claude model that plan buys.
#: Decided 2026-11-09 with the launch prices: Free and Pro answer on Haiku, Estate on Sonnet. The
#: AI allowances are sized to this — Pro's 1,500 calls cost about $5 a month on Haiku and would
#: cost about $165 on Sonnet, against a $15 price — so changing a model here means re-checking
#: `ai_calls_per_month` too.
PLAN_MODELS = {
    "standard": "claude-haiku-5-5",
    "priority": DEFAULT_MODEL,
}


def plan_model(session: Session) -> str:
    """The Claude model the bound account's plan buys.

    No bound account means an operator CLI command or a system job (N10: not a household's
    usage), which gets `DEFAULT_MODEL`. An account that cannot be read gets the *standard* model:
    the cheaper answer is the fail-closed direction for a paid feature (`PRICING` rule 2).
    """
    from mihomes.entitlements.limits import limits_for
    from mihomes.models.account import Account
    from mihomes.tenancy.context import current_account

    try:
        account_id = current_account.get()
    except LookupError:
        return DEFAULT_MODEL
    try:
        account = session.get(Account, account_id)
    except Exception:
        account = None
    if account is None:
        return PLAN_MODELS["standard"]
    priority = limits_for(account.plan, account.subscription_status)["ai_priority"]
    return PLAN_MODELS.get(priority, PLAN_MODELS["standard"])


def get_ai_model(session: Session, provider: str = "nim") -> str:
    """Get configured model name with sensible defaults.

    Checks ai.<provider>_model first, then ai.model, then built-in defaults. Both keys are
    operator-only (`config_service.USER_SETTING_KEYS` excludes `ai.*`), so an explicit setting is
    an operator's override and wins; with none, Claude's model follows the account's plan.
    """
    provider_specific = get_config(session, f"ai.{provider}_model")
    if provider_specific:
        return provider_specific
    stored = get_config(session, "ai.model")
    if stored:
        return stored
    if provider == "claude":
        return plan_model(session)
    defaults = {
        "claude": DEFAULT_MODEL,
        "openai": "gpt-4o",
        "ollama": "llama3.1",
        "nim": "meta/muse-glimmer-30b",
    }
    return defaults.get(provider, DEFAULT_MODEL)
