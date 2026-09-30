import random
import subprocess
from collections.abc import Iterator
from typing import Any
from unittest.mock import Mock, patch

import pytest

import gha_harden

OLD_SHA = "a" * 40
NEW_SHA = "b" * 40
BAD_SHA = "e" * 40
OLD_TAG = "v1.0.0"
NEW_TAG = "v2.10.0"
BAD_TAG = "v2.4.1"

TAGS = [
    {"name": "v3.0.0-rc.1", "commit": {"sha": "c" * 40}},
    {"name": "v2", "commit": {"sha": NEW_SHA}},
    {"name": "v2.9.0", "commit": {"sha": "d" * 40}},
    {"name": NEW_TAG, "commit": {"sha": NEW_SHA}},
    {"name": "v1", "commit": {"sha": OLD_SHA}},
    {"name": OLD_TAG, "commit": {"sha": OLD_SHA}},
]


@pytest.mark.parametrize(
    ("upgrade", "pin", "curr_ref", "exp_ref", "exp_annotation", "exp_skipped"),
    [
        # no upgrade, no pin
        (False, False, "v1", "v1", None, False),
        (False, False, "v2", "v2", None, False),
        (False, False, OLD_TAG, OLD_TAG, None, False),
        (False, False, NEW_TAG, NEW_TAG, None, False),
        (False, False, OLD_SHA, OLD_SHA, OLD_TAG, False),
        (False, False, NEW_SHA, NEW_SHA, NEW_TAG, False),
        (False, False, BAD_TAG, BAD_TAG, None, False),
        (False, False, BAD_SHA, BAD_SHA, None, False),
        # no upgrade, pin
        (False, True, "v1", OLD_SHA, OLD_TAG, False),
        (False, True, "v2", NEW_SHA, NEW_TAG, False),
        (False, True, OLD_TAG, OLD_SHA, OLD_TAG, False),
        (False, True, NEW_TAG, NEW_SHA, NEW_TAG, False),
        (False, True, OLD_SHA, OLD_SHA, OLD_TAG, False),
        (False, True, NEW_SHA, NEW_SHA, NEW_TAG, False),
        (False, True, BAD_TAG, BAD_TAG, None, True),
        (False, True, BAD_SHA, BAD_SHA, None, False),
        # upgrade, no pin
        (True, False, "v1", "v2", None, False),
        (True, False, "v2", "v2", None, False),
        (True, False, "v1.2", NEW_TAG, None, False),
        (True, False, OLD_TAG, NEW_TAG, None, False),
        (True, False, NEW_TAG, NEW_TAG, None, False),
        (True, False, OLD_SHA, NEW_SHA, NEW_TAG, False),
        (True, False, NEW_SHA, NEW_SHA, NEW_TAG, False),
        (True, False, BAD_TAG, NEW_TAG, None, False),
        (True, False, BAD_SHA, NEW_SHA, NEW_TAG, False),
        # upgrade, pin
        (True, True, "v1", NEW_SHA, NEW_TAG, False),
        (True, True, "v2", NEW_SHA, NEW_TAG, False),
        (True, True, OLD_TAG, NEW_SHA, NEW_TAG, False),
        (True, True, NEW_TAG, NEW_SHA, NEW_TAG, False),
        (True, True, OLD_SHA, NEW_SHA, NEW_TAG, False),
        (True, True, NEW_SHA, NEW_SHA, NEW_TAG, False),
        (True, True, BAD_TAG, NEW_SHA, NEW_TAG, False),
        (True, True, BAD_SHA, NEW_SHA, NEW_TAG, False),
    ],
    ids=[
        # no upgrade, no pin
        "no_upgrade-no_pin-old_short_tag",
        "no_upgrade-no_pin-new_short_tag",
        "no_upgrade-no_pin-old_long_tag",
        "no_upgrade-no_pin-new_long_tag",
        "no_upgrade-no_pin-old_sha",
        "no_upgrade-no_pin-new_sha",
        "no_upgrade-no_pin-bad_tag",
        "no_upgrade-no_pin-bad_sha",
        # no upgrade, pin
        "no_upgrade-pin-old_short_tag",
        "no_upgrade-pin-new_short_tag",
        "no_upgrade-pin-old_long_tag",
        "no_upgrade-pin-new_long_tag",
        "no_upgrade-pin-old_sha",
        "no_upgrade-pin-new_sha",
        "no_upgrade-pin-bad_tag",
        "no_upgrade-pin-bad_sha",
        # upgrade, no pin
        "upgrade-no_pin-old_short_tag",
        "upgrade-no_pin-new_short_tag",
        "upgrade-no_pin-minor_tag",
        "upgrade-no_pin-old_long_tag",
        "upgrade-no_pin-new_long_tag",
        "upgrade-no_pin-old_sha",
        "upgrade-no_pin-new_sha",
        "upgrade-no_pin-bad_tag",
        "upgrade-no_pin-bad_sha",
        # upgrade, pin
        "upgrade-pin-old_short_tag",
        "upgrade-pin-new_short_tag",
        "upgrade-pin-old_long_tag",
        "upgrade-pin-new_long_tag",
        "upgrade-pin-old_sha",
        "upgrade-pin-new_sha",
        "upgrade-pin-bad_tag",
        "upgrade-pin-bad_sha",
    ],
)
def test_update_line(
    upgrade: bool,
    pin: bool,
    curr_ref: str,
    exp_ref: str,
    exp_annotation: str | None,
    exp_skipped: bool,
) -> None:
    ctx = gha_harden.Context(upgrade, pin, {}, Mock())

    def side_effect(path: str, **kwargs) -> Any:
        try:
            return {
                "repos/actions/example/tags?per_page=100": [TAGS],
                "repos/actions/example/commits/v1": {"sha": OLD_SHA},
                "repos/actions/example/commits/v2": {"sha": NEW_SHA},
                "repos/actions/example/commits/v1.0.0": {"sha": OLD_SHA},
                "repos/actions/example/commits/v2.10.0": {"sha": NEW_SHA},
            }[path]
        except KeyError:
            raise subprocess.CalledProcessError(1, path)

    lf = random.choice(["\n", "\r", "\r\n"])

    with patch("gha_harden.gh_api", side_effect=side_effect):
        result = gha_harden.update_line(ctx, f"  - uses: actions/example@{curr_ref}{lf}")

    comment = f" # {exp_annotation}" if exp_annotation is not None else ""
    if exp_skipped:
        assert ctx.skipped == {f"actions/example@{curr_ref}": "GitHub API request failed"}
    else:
        assert ctx.skipped == {}
    assert result == f"  - uses: actions/example@{exp_ref}{comment}{lf}"


@pytest.fixture(autouse=True)
def clear_caches() -> Iterator[None]:
    gha_harden.get_tags.cache_clear()
    gha_harden.get_commit_details.cache_clear()
    yield
    gha_harden.get_tags.cache_clear()
    gha_harden.get_commit_details.cache_clear()
