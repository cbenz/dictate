"""Plugin registry — abstract source of plugins.

Each plugin is a directory (flat, no type subdirs):
  ~/.config/tiny-dictate/plugins/<name>/
    plugin.py     — exposes create(config) and/or create_<type>(config)
    manifest.json  — name, description, provides, dependencies, version

Plugin dependencies listed in manifest.json are installed directly into the
application's Python environment via pip when the plugin is installed.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import override

from .config import PLUGINS_DIR, AppConfig, RegistryDef

__all__ = [
    "GithubRepoPluginRegistry",
    "LocalDirectoryPluginRegistry",
    "PluginInfo",
    "PluginRegistry",
    "create_registry",
    "list_installed",
    "uninstall_plugin",
]


logger = logging.getLogger(__name__)

# Map plugin type to factory function name in plugin.py
FACTORY_NAMES = {
    "transcription_backend": "create_transcription_backend",
    "transcription_injector": "create_transcription_injector",
    "feedback_notifier": "create_feedback_notifier",
}


class PluginInfo:
    """Metadata about a plugin from its manifest.json."""

    def __init__(
        self,
        name: str,
        description: str = "",
        version: str = "0.0.0",
        provides: list[str] | None = None,
        dependencies: list[str] | None = None,
    ) -> None:
        self.name: str = name
        self.description: str = description
        self.version: str = version
        self.provides: list[str] = provides or []
        self.dependencies: list[str] = dependencies or []

    @property
    def id(self) -> str:
        return self.name

    @classmethod
    def from_manifest(cls, manifest_path: Path) -> PluginInfo | None:
        try:
            data = json.loads(manifest_path.read_text())
            return cls(
                name=data["name"],
                description=data.get("description", ""),
                version=data.get("version", "0.0.0"),
                provides=data.get("provides", []),
                dependencies=data.get("dependencies", []),
            )
        except (json.JSONDecodeError, KeyError, FileNotFoundError) as exc:
            logger.warning("Invalid manifest at %s: %s", manifest_path, exc)
            return None

    @classmethod
    def from_directory(cls, plugin_dir: Path) -> PluginInfo | None:
        """Read plugin info from index.json in the parent directory."""
        # Resolve symlinks to find the source plugins directory
        resolved = plugin_dir.resolve() if plugin_dir.is_symlink() else plugin_dir
        index_path = resolved.parent / "index.json"
        if not index_path.exists():
            # Fallback: try parent of the unresolved path
            index_path = plugin_dir.parent / "index.json"
        if not index_path.exists():
            return None
        try:
            data = json.loads(index_path.read_text())
        except json.JSONDecodeError, OSError:
            return None
        for entry in data:
            if entry.get("name") == plugin_dir.name:
                return cls(
                    name=entry["name"],
                    description=entry.get("description", ""),
                    version=entry.get("version", "0.0.0"),
                    provides=entry.get("provides", []),
                    dependencies=entry.get("dependencies", []),
                )
        return None


# ═════════════════════════════════════════════════════════════
#  VENV MANAGEMENT
# ═════════════════════════════════════════════════════════════


def _install_deps(plugin_dir: Path, dependencies: list[str]) -> None:
    """Install plugin dependencies into a dedicated venv.

    Uses --no-deps so only the plugin's own package is installed in the venv.
    Transitive dependencies are resolved from the app's main venv via sys.path.
    """
    if not dependencies:
        return
    venv_dir = plugin_dir / "venv"
    if not (venv_dir / "bin" / "python").exists():
        logger.info("Creating venv for plugin %s", plugin_dir.name)
        subprocess.run(
            [sys.executable, "-m", "venv", "--system-site-packages", str(venv_dir)],
            check=True,
            capture_output=True,
            timeout=60,
        )
    pip = venv_dir / "bin" / "pip"
    logger.info("Installing plugin dependencies: %s", dependencies)
    subprocess.run(
        [str(pip), "install", *dependencies],
        check=True,
        capture_output=True,
        timeout=120,
    )
    logger.info("Dependencies installed")


def plugin_venv_site_packages(plugin_dir: Path) -> Path | None:
    """Return the site-packages path of a plugin's venv, or None."""
    venv_dir = plugin_dir / "venv"
    if not venv_dir.exists():
        return None
    candidates = [
        venv_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages",
        venv_dir / "lib" / "site-packages",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


class PluginRegistry(ABC):
    """Abstract source of plugins."""

    @abstractmethod
    @override
    async def list_plugins(self) -> list[PluginInfo]: ...

    @abstractmethod
    @override
    async def install_plugin(self, plugin_id: str) -> None: ...

    def _plugin_dir(self, name: str) -> Path:
        return PLUGINS_DIR / name


# ═════════════════════════════════════════════════════════════
#  LOCAL DIRECTORY (dev mode)
# ═════════════════════════════════════════════════════════════


class LocalDirectoryPluginRegistry(PluginRegistry):
    _source: Path
    """Plugins stored in a local directory. Install = symlink + venv setup."""

    def __init__(self, source_dir: str | Path) -> None:
        self._source = Path(source_dir)

    @override
    async def list_plugins(self) -> list[PluginInfo]:
        results = []
        if not self._source.exists():
            return results
        for plugin_dir in sorted(self._source.iterdir()):
            if not plugin_dir.is_dir():
                continue
            info = PluginInfo.from_directory(plugin_dir)
            if info:
                results.append(info)
        return results

    @override
    async def install_plugin(self, plugin_id: str) -> None:
        src = self._source / plugin_id
        dst = self._plugin_dir(plugin_id)

        if not (src / "plugin.py").exists():
            msg = f"Plugin {plugin_id} not found at {src}"
            raise FileNotFoundError(msg)

        dst.parent.mkdir(parents=True, exist_ok=True)

        if dst.exists():
            # Plugin already installed — check if venv is missing
            venv_dir = dst / "venv"
            info = PluginInfo.from_directory(dst)
            if info and info.dependencies and not (venv_dir / "bin" / "python").exists():
                _install_deps(dst, info.dependencies)
                logger.info("Recreated missing venv for %s", plugin_id)
                return
            msg_0 = f"Plugin {plugin_id} already installed"
            raise FileExistsError(msg_0)

        Path(str(dst)).symlink_to(str(src.resolve()))

        # Set up venv for the plugin's dependencies
        info = PluginInfo.from_directory(dst)
        if info and info.dependencies:
            _install_deps(dst, info.dependencies)

        logger.info("Installed plugin %s (symlink → %s)", plugin_id, src)


# ═════════════════════════════════════════════════════════════
#  GITHUB REPO (prod mode)
# ═════════════════════════════════════════════════════════════


class GithubRepoPluginRegistry(PluginRegistry):
    """Plugins hosted on GitHub. Install = download + venv setup."""

    RAW_BASE = "https://raw.githubusercontent.com"

    def __init__(self, repo: str, branch: str = "main", plugins_path: str = "plugins") -> None:
        self._repo = repo
        self._branch = branch
        self._plugins_path = plugins_path

    @override
    async def list_plugins(self) -> list[PluginInfo]:
        import urllib.request

        index_url = f"{self.RAW_BASE}/{self._repo}/{self._branch}/{self._plugins_path}/index.json"
        try:
            with urllib.request.urlopen(index_url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                return [PluginInfo(**item) for item in data]
        except Exception as exc:
            logger.warning("Failed to fetch plugin index: %s", exc)
            return []

    @override
    async def install_plugin(self, plugin_id: str) -> None:
        import urllib.request

        dst = self._plugin_dir(plugin_id)
        if dst.exists():
            # Plugin already installed — check if venv is missing
            venv_dir = dst / "venv"
            index_path = dst / ".." / "index.json"
            if index_path.exists():
                import json

                data = json.loads(index_path.read_text())
                for entry in data:
                    if entry.get("name") == plugin_id:
                        deps = entry.get("dependencies", [])
                        if deps and not (venv_dir / "bin" / "python").exists():
                            _install_deps(dst, deps)
                            logger.info("Recreated missing venv for %s", plugin_id)
                            return
                        break
            msg = f"Plugin {plugin_id} already installed"
            raise FileExistsError(msg)

        # Find plugin info from index.json
        index_url = f"{self.RAW_BASE}/{self._repo}/{self._branch}/{self._plugins_path}/index.json"
        dst.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(index_url, timeout=10) as resp:
                index_data = json.loads(resp.read().decode())
        except Exception as exc:
            shutil.rmtree(dst, ignore_errors=True)
            msg = f"Failed to fetch plugin index: {exc}"
            raise RuntimeError(msg) from exc

        # Find the plugin entry
        plugin_info = None
        for entry in index_data:
            if entry.get("name") == plugin_id:
                plugin_info = entry
                break

        if plugin_info is None:
            shutil.rmtree(dst, ignore_errors=True)
            msg = f"Plugin {plugin_id} not found in index"
            raise FileNotFoundError(msg)

        # Download plugin files
        files = plugin_info.get("files", ["plugin.py"])
        deps = plugin_info.get("dependencies", [])
        for filename in files:
            file_url = f"{self.RAW_BASE}/{self._repo}/{self._branch}/{self._plugins_path}/{plugin_id}/{filename}"
            try:
                with urllib.request.urlopen(file_url, timeout=10) as resp:
                    (dst / filename).write_bytes(resp.read())
            except Exception as exc:
                shutil.rmtree(dst, ignore_errors=True)
                msg = f"Failed to download {filename} for {plugin_id}: {exc}"
                raise RuntimeError(msg) from exc

        # Set up venv
        if deps:
            _install_deps(dst, deps)

        logger.info("Installed plugin %s from GitHub", plugin_id)

        # Set up venv
        deps = manifest.get("dependencies", [])
        if deps:
            _install_deps(dst, deps)

        logger.info("Installed plugin %s from GitHub", plugin_id)


# ═════════════════════════════════════════════════════════════
#  FACTORY
# ═════════════════════════════════════════════════════════════


def _parse_github_repo(url: str) -> tuple[str, str]:
    """Extract owner/repo from a GitHub URL."""
    url = url.rstrip("/")
    if url.startswith("https://github.com/"):
        parts = url[len("https://github.com/") :].split("/", 1)
        if len(parts) == 2:
            return parts[0], parts[1].replace(".git", "")
    # Already in owner/repo format
    if "/" in url and not url.startswith("http"):
        return url.split("/", 1)[0], url.split("/", 1)[1].replace(".git", "")
    msg = f"Invalid GitHub repo URL: {url}"
    raise ValueError(msg)


def create_registry(config: AppConfig, registry_id: str | None = None) -> PluginRegistry:
    """Create a plugin registry from config.

    Reads registries from config.toml under [registries].
    If registry_id is given, uses that; otherwise uses the default.
    """
    reg = config.registries
    reg_id = registry_id or reg.default
    if not reg_id:
        msg = (
            "No registry configured. Set a default in [registries] in config.toml.\n"
            "See: tiny-dictate config print-default"
        )
        raise ValueError(msg)

    # Look up the registry definition
    reg_def = None
    for field_name in reg.model_fields_set:
        if field_name == "default":
            continue
        val = getattr(reg, field_name, None)
        if isinstance(val, RegistryDef) and field_name == reg_id:
            reg_def = val
            break

    if reg_def is None and reg.model_extra and reg_id in reg.model_extra:
        extra = reg.model_extra[reg_id]
        reg_def = RegistryDef(**extra) if isinstance(extra, dict) else extra

    if reg_def is None:
        msg = f"Registry '{reg_id}' not found in config.toml [registries]"
        raise ValueError(msg)

    if reg_def.type == "directory":
        if not reg_def.path:
            msg = f"Registry '{reg_id}' has no 'path' setting"
            raise ValueError(msg)
        return LocalDirectoryPluginRegistry(reg_def.path)

    if reg_def.type == "github":
        if not reg_def.repo:
            msg = f"Registry '{reg_id}' has no 'repo' setting"
            raise ValueError(msg)
        owner, repo_name = _parse_github_repo(reg_def.repo)
        return GithubRepoPluginRegistry(f"{owner}/{repo_name}", reg_def.branch, reg_def.plugins_path)

    msg = f"Registry '{reg_id}': unknown type '{reg_def.type}'"
    raise ValueError(msg)


# ═════════════════════════════════════════════════════════════
#  MANAGER
# ═════════════════════════════════════════════════════════════


def list_installed() -> list[PluginInfo]:
    """List currently installed plugins."""
    results = []
    if not PLUGINS_DIR.exists():
        return results
    for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
        if not plugin_dir.is_dir():
            continue
        info = PluginInfo.from_directory(plugin_dir)
        if info:
            results.append(info)
    return results


def uninstall_plugin(plugin_id: str) -> None:
    """Remove an installed plugin."""
    dst = PLUGINS_DIR / plugin_id
    if not dst.exists():
        msg = f"Plugin {plugin_id} is not installed"
        raise FileNotFoundError(msg)
    if dst.is_symlink():
        dst.unlink()
    else:
        shutil.rmtree(dst)
    logger.info("Uninstalled plugin %s", plugin_id)
