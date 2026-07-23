"""Dynamic loading of plugins from the user config directory.

Plugins are stored under XDG_CONFIG_HOME/tiny-dictate/plugins/<name>/.
Each exposes one or more factory functions: create(config), create_backend(config), etc.
Each plugin has its own venv at <plugin_dir>/venv/ for its dependencies.
"""

from __future__ import annotations

import importlib.util
import logging
import sys

from .config import PLUGINS_DIR
from .plugin_registry import FACTORY_NAMES, plugin_venv_site_packages

__all__ = ["load_plugin"]


logger = logging.getLogger(__name__)


def _load_module(name: str):
    """Dynamically import a plugin module from the user plugin directory."""
    module_path = PLUGINS_DIR / name / "plugin.py"
    if not module_path.exists():
        msg = f"Plugin {name} not found at {module_path}. Install it with: tiny-dictate plugins install {name}"
        raise FileNotFoundError(msg)

    # Add the plugin's venv site-packages to sys.path for dependency resolution
    venv_site = plugin_venv_site_packages(module_path.parent)
    if venv_site and str(venv_site) not in sys.path:
        sys.path.insert(0, str(venv_site))

    spec = importlib.util.spec_from_file_location(f"tiny_dictate_plugins.{name}", module_path)
    if spec is None or spec.loader is None:
        msg = f"Failed to load plugin {name}: invalid module spec"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_plugin(plugin_type: str, name: str, config: dict[str, Any]) -> Any:
    """Load a plugin and call the appropriate factory function.

    Looks for a type-specific factory (create_backend, create_injector, create_notifier)
    and falls back to the generic create(config).
    """
    module = _load_module(name)

    # Try type-specific factory first, then generic create()
    factory_name = FACTORY_NAMES.get(plugin_type, "create")
    factory = getattr(module, factory_name, None) or getattr(module, "create", None)

    if factory is None:
        msg = f"Plugin {name} has no {factory_name}() or create() factory function"
        raise AttributeError(msg)
    logger.info("Loaded plugin %s (%s)", name, plugin_type)
    return factory(config)
