"""Brand images, translations and services of the integration."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
import pytest
import yaml

INTEGRATION = Path(__file__).parents[1] / "custom_components" / "wake_word_recorder"
BRAND = INTEGRATION / "brand"


@pytest.mark.parametrize("name", ["icon", "dark_icon", "logo", "dark_logo"])
def test_brand_image_and_retina_variant(name: str) -> None:
    image = Image.open(BRAND / f"{name}.png")
    retina = Image.open(BRAND / f"{name}@2x.png")
    assert image.height == 256
    assert retina.size == (image.width * 2, image.height * 2)
    if name.endswith("icon"):
        assert image.width == 256


def _keys(data, prefix=""):
    if isinstance(data, dict):
        return {k for key, value in data.items() for k in _keys(value, f"{prefix}{key}.")}
    return {prefix.rstrip(".")}


def test_translations_match_strings() -> None:
    strings = json.loads((INTEGRATION / "strings.json").read_text())
    assert json.loads((INTEGRATION / "translations" / "en.json").read_text()) == strings
    polish = json.loads((INTEGRATION / "translations" / "pl.json").read_text(encoding="utf-8"))
    assert _keys(polish) == _keys(strings)


def test_every_service_is_described() -> None:
    services = yaml.safe_load((INTEGRATION / "services.yaml").read_text())
    strings = json.loads((INTEGRATION / "strings.json").read_text())["services"]
    assert set(services) == set(strings)
    for name, spec in services.items():
        assert set((spec or {}).get("fields", {})) == set(strings[name].get("fields", {}))
