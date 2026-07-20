from __future__ import annotations

import builtins
import copy
import json
import random

from pa_agent.ai.json_validator import JsonValidator, Ok, ValidationError
from pa_agent.config.settings import Settings
from tests.integration.conftest import VALID_STAGE1, make_frame


def test_schema_validation_fails_closed_when_jsonschema_is_missing(
    monkeypatch,
) -> None:
    validator = JsonValidator(Settings())
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "jsonschema":
            raise ImportError("fault injection")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = validator.validate(
        "stage1",
        json.dumps(VALID_STAGE1, ensure_ascii=False),
        kline_frame=make_frame(),
    )

    assert isinstance(result, ValidationError)
    assert result.invalid_fields == ["validator_dependency:jsonschema"]


def test_legal_json_surface_perturbations_have_sub_one_percent_false_hard_rate() -> None:
    validator = JsonValidator(Settings())
    rng = random.Random(20260720)
    failures = 0

    for index in range(1000):
        payload = copy.deepcopy(VALID_STAGE1)
        items = list(payload.items())
        rng.shuffle(items)
        raw = json.dumps(
            dict(items),
            ensure_ascii=False,
            indent=rng.choice([None, 2, 4]),
            separators=None if rng.random() < 0.5 else (",", ":"),
        )
        wrapper = index % 5
        if wrapper == 0:
            raw = f"```json\n{raw}\n```"
        elif wrapper == 1:
            raw = f"Analysis result:\n{raw}\nEnd."
        elif wrapper == 2:
            raw = f"\n\n{raw}\n```"
        elif wrapper == 3:
            raw = f"```JSON\n{raw}\n```"

        result = validator.validate(
            "stage1",
            raw,
            kline_frame=make_frame(),
        )
        failures += int(not isinstance(result, Ok))

    assert failures < 10
