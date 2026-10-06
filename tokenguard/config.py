"""Config loading for TokenGuard: budgets.yaml (or budgets.json)."""

import json
import math
import os

from . import yaml_subset


class ConfigError(Exception):
    """Raised when the config file is missing or invalid."""


DEFAULT_CONFIG_NAMES = ("budgets.yaml", "budgets.yml", "budgets.json")
DEFAULT_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".tokenguard")


class Config:
    """Validated TokenGuard configuration."""

    def __init__(self, raw, source):
        if not isinstance(raw, dict):
            raise ConfigError(
                "config %s: top level must be a mapping, got %s"
                % (source, type(raw).__name__)
            )
        self.source = source
        self.keys = _budgets_section(raw.get("keys"), "keys", source)
        self.projects = _budgets_section(raw.get("projects"), "projects", source)
        self.alert_at = _alert_thresholds(raw.get("alert_at", [0.8, 0.95]), source)
        self.webhook_url = raw.get("webhook_url") or None
        if self.webhook_url is not None and not isinstance(self.webhook_url, str):
            raise ConfigError(
                "config %s: webhook_url must be a string" % source
            )
        self.prices = _prices_section(raw.get("prices"), source)

    def __repr__(self):  # pragma: no cover - debugging helper
        return (
            "Config(keys=%d, projects=%d, alert_at=%s, webhook=%s)"
            % (
                len(self.keys),
                len(self.projects),
                self.alert_at,
                bool(self.webhook_url),
            )
        )


def _as_number(value, what, source):
    """Coerce a config value to a finite float.

    Rejects booleans (float(True) == 1.0 would silently invent a $1 budget)
    and NaN/inf (a NaN budget can never alert or breach).
    """
    if isinstance(value, bool):
        raise ConfigError(
            "config %s: %s must be a number, got %r" % (source, what, value)
        )
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ConfigError(
            "config %s: %s must be a number, got %r" % (source, what, value)
        )
    if not math.isfinite(number):
        raise ConfigError(
            "config %s: %s must be finite, got %r" % (source, what, value)
        )
    return number


def _budgets_section(section, name, source):
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ConfigError(
            "config %s: '%s' must be a mapping" % (source, name)
        )
    budgets = {}
    for entity, body in section.items():
        if not isinstance(body, dict) or "monthly_usd" not in body:
            raise ConfigError(
                "config %s: '%s.%s' must define monthly_usd"
                % (source, name, entity)
            )
        amount = _as_number(
            body["monthly_usd"], "'%s.%s.monthly_usd'" % (name, entity), source
        )
        if amount <= 0:
            raise ConfigError(
                "config %s: '%s.%s.monthly_usd' must be > 0"
                % (source, name, entity)
            )
        budgets[str(entity)] = amount
    return budgets


def _alert_thresholds(value, source):
    if not isinstance(value, (list, tuple)):
        raise ConfigError("config %s: alert_at must be a list" % source)
    thresholds = []
    for item in value:
        t = _as_number(item, "alert_at entries", source)
        if not 0 < t < 1:
            raise ConfigError(
                "config %s: alert_at entries must be between 0 and 1 "
                "(exclusive)" % source
            )
        thresholds.append(t)
    return sorted(set(thresholds))


def _prices_section(section, source):
    """Normalize the optional price table.

    prices:
      default: {input_per_1k: 0.0015, output_per_1k: 0.006}
      models:
        gpt-4o-mini: {input_per_1k: ..., output_per_1k: ...}
    """
    if section is None:
        return {"default": None, "models": {}}
    if not isinstance(section, dict):
        raise ConfigError("config %s: prices must be a mapping" % source)
    default = _price_entry(section.get("default"), "prices.default", source)
    models = {}
    raw_models = section.get("models") or {}
    if not isinstance(raw_models, dict):
        raise ConfigError("config %s: prices.models must be a mapping" % source)
    for model, entry in raw_models.items():
        models[str(model)] = _price_entry(
            entry, "prices.models.%s" % model, source
        )
    return {"default": default, "models": models}


def _price_entry(entry, where, source):
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise ConfigError("config %s: %s must be a mapping" % (source, where))
    try:
        in_price = _as_number(
            entry["input_per_1k"], "%s.input_per_1k" % where, source
        )
        out_price = _as_number(
            entry["output_per_1k"], "%s.output_per_1k" % where, source
        )
    except KeyError:
        raise ConfigError(
            "config %s: %s needs numeric input_per_1k and output_per_1k"
            % (source, where)
        )
    if in_price < 0 or out_price < 0:
        raise ConfigError("config %s: %s prices must be >= 0" % (source, where))
    return {"input_per_1k": in_price, "output_per_1k": out_price}


def find_config(explicit=None):
    """Resolve the config path: explicit flag > env > cwd > ~/.tokenguard."""
    if explicit:
        return explicit
    env_path = os.environ.get("TOKENGUARD_CONFIG")
    if env_path:
        return env_path
    for name in DEFAULT_CONFIG_NAMES:
        if os.path.isfile(name):
            return name
    for name in DEFAULT_CONFIG_NAMES:
        candidate = os.path.join(DEFAULT_CONFIG_DIR, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def load_config(path):
    """Load and validate a config file. Detects JSON vs YAML by content."""
    if not os.path.isfile(path):
        raise ConfigError("config file not found: %s" % path)
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    stripped = text.lstrip()
    try:
        if stripped.startswith("{") or stripped.startswith("["):
            raw = json.loads(text)
        else:
            raw = yaml_subset.loads(text)
    except ValueError as exc:
        raise ConfigError("config %s: parse error: %s" % (path, exc))
    return Config(raw, path)
