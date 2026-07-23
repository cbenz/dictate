"""Configuration management for tiny-dictate.

Uses Pydantic for parsing and validation.
"""

from __future__ import annotations

import os
import pathlib
import tomllib
from typing import Literal

import pydantic
import xdg_base_dirs

__all__ = [
    "CONFIG_DIR",
    "CONFIG_PATH",
    "PLUGINS_DIR",
    "RUNTIME_DIR",
    "AppConfig",
    "RegistryDef",
    "api_key",
    "load",
    "print_default",
    "save",
]


XDG_CONFIG_HOME = xdg_base_dirs.xdg_config_home()
XDG_RUNTIME_DIR = xdg_base_dirs.xdg_runtime_dir()

CONFIG_DIR = XDG_CONFIG_HOME / "tiny-dictate"
CONFIG_PATH = CONFIG_DIR / "config.toml"
PLUGINS_DIR = XDG_CONFIG_HOME / "tiny-dictate" / "plugins"
RUNTIME_DIR = XDG_RUNTIME_DIR / "tiny-dictate"


# ═════════════════════════════════════════════════════════════
#  PYDANTIC MODELS
# ═════════════════════════════════════════════════════════════


class AudioConfig(pydantic.BaseModel):
    default_source: str | None = None


class TranscriptionBackendConfig(pydantic.BaseModel):
    mode: Literal["realtime", "batch"] = "realtime"
    plugin: str = "elevenlabs"


class TranscriptionInjectorConfig(pydantic.BaseModel):
    paste_keys: str = "ctrl-v"
    plugin: str = "clipboard"


class FeedbackNotifierConfig(pydantic.BaseModel):
    plugins: list[str] = []


class RegistryDef(pydantic.BaseModel):
    type: Literal["github", "directory"]
    repo: str | None = None
    path: str | None = None
    branch: str = "main"
    plugins_path: str = "plugins"


class RegistriesConfig(pydantic.BaseModel):
    default: str = "github"
    github: RegistryDef = pydantic.Field(
        default_factory=lambda: RegistryDef(
            type="github",
            repo="https://github.com/cbenz/tiny-dictate",
        )
    )
    # Allow extra registries via __pydantic_extra__
    model_config = pydantic.ConfigDict(extra="allow")


class AppConfig(pydantic.BaseModel):
    """Top-level validated config."""

    audio: AudioConfig = pydantic.Field(default_factory=AudioConfig)
    transcription_backend: TranscriptionBackendConfig = pydantic.Field(default_factory=TranscriptionBackendConfig)
    transcription_injector: TranscriptionInjectorConfig = pydantic.Field(default_factory=TranscriptionInjectorConfig)
    feedback_notifier: FeedbackNotifierConfig = pydantic.Field(default_factory=FeedbackNotifierConfig)
    registries: RegistriesConfig = pydantic.Field(default_factory=RegistriesConfig)


# ═════════════════════════════════════════════════════════════
#  LOAD / SAVE
# ═════════════════════════════════════════════════════════════


def load() -> AppConfig:
    """Load and validate config from ~/.config/tiny-dictate/config.toml."""
    if CONFIG_PATH.exists():
        with pathlib.Path(CONFIG_PATH).open("rb") as f:
            raw = tomllib.load(f)
    else:
        raw = {}
    try:
        return AppConfig.model_validate(raw)
    except pydantic.ValidationError as exc:
        for error in exc.errors():
            loc = " → ".join(str(l) for l in error["loc"])
        raise SystemExit(1) from exc


def save(config: AppConfig) -> None:
    """Save config as TOML."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(config_to_toml(config))


def config_to_toml(config: AppConfig) -> str:
    """Render an AppConfig as TOML."""
    lines: list[str] = []
    data = config.model_dump(exclude_defaults=False)

    for section, values in data.items():
        if isinstance(values, dict):
            lines.append(f"\n[{section}]")
            for key, val in values.items():
                if isinstance(val, dict):
                    lines.append(f"\n[{section}.{key}]")
                    for k, v in val.items():
                        if v is None:
                            lines.append(f'# {k} = ""')
                        elif isinstance(v, bool):
                            lines.append(f"{k} = {'true' if v else 'false'}")
                        elif isinstance(v, int):
                            lines.append(f"{k} = {v}")
                        else:
                            lines.append(f'{k} = "{v}"')
                elif val is None:
                    lines.append(f'# {key} = ""')
                elif isinstance(val, bool):
                    lines.append(f"{key} = {'true' if val else 'false'}")
                elif isinstance(val, int):
                    lines.append(f"{key} = {val}")
                else:
                    lines.append(f'{key} = "{val}"')
        else:
            lines.append(f"\n[{section}]")
            lines.append(f'{section} = "{values}"')

    return "\n".join(lines)


def print_default() -> None:
    """Print the default config as TOML to stdout."""


def api_key() -> str | None:
    """Return ElevenLabs API key from environment."""
    return os.getenv("ELEVENLABS_API_KEY")
