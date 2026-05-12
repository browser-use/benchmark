"""Browser provider registry."""

import importlib

from browsers.util import retry_on_429
from browsers import (
    anchor,
    browser_use_cloud,
    browserbase,
    browserless,
    driver,
    hyperbrowser,
    local_headful,
    local_headless,
    onkernel,
    rebrowser,
    steel,
)

PROVIDERS = [
    "anchor",
    "browserbase",
    "browserless",
    "driver",
    "hyperbrowser",
    "local_headful",
    "local_headless",
    "onkernel",
    "rebrowser",
    "steel",
]

BROWSERS = {
    "anchor": anchor,
    "browser-use-cloud": browser_use_cloud,
    "browserbase": browserbase,
    "browserless": browserless,
    "driver": driver,
    "hyperbrowser": hyperbrowser,
    "local_headful": local_headful,
    "local_headless": local_headless,
    "onkernel": onkernel,
    "rebrowser": rebrowser,
    "steel": steel,
}


def get_provider(name: str):
    """Import and return a browser provider module by name."""
    if name not in PROVIDERS:
        raise ValueError(f"Unknown browser provider: {name}. Available: {PROVIDERS}")
    return importlib.import_module(f"browsers.{name}")
