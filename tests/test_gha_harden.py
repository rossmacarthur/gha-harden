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
    ("upgrade", "curr_ref", "exp_ref", "exp_annotation", "exp_skipped"),
    [
        # no upgrade
        (False, "v1", OLD_SHA, OLD_TAG, False),
        (False, "v2", NEW_SHA, NEW_TAG, False),
        (False, OLD_TAG, OLD_SHA, OLD_TAG, False),
        (False, NEW_TAG, NEW_SHA, NEW_TAG, False),
        (False, OLD_SHA, OLD_SHA, OLD_TAG, False),
        (False, NEW_SHA, NEW_SHA, NEW_TAG, False),
        (False, BAD_TAG, BAD_TAG, None, True),
        (False, BAD_SHA, BAD_SHA, None, False),
        # upgrade
        (True, "v1", NEW_SHA, NEW_TAG, False),
        (True, "v2", NEW_SHA, NEW_TAG, False),
        (True, OLD_TAG, NEW_SHA, NEW_TAG, False),
        (True, NEW_TAG, NEW_SHA, NEW_TAG, False),
        (True, OLD_SHA, NEW_SHA, NEW_TAG, False),
        (True, NEW_SHA, NEW_SHA, NEW_TAG, False),
        (True, BAD_TAG, NEW_SHA, NEW_TAG, False),
        (True, BAD_SHA, NEW_SHA, NEW_TAG, False),
    ],
    ids=[
        # no upgrade
        "no_upgrade-old_short_tag",
        "no_upgrade-new_short_tag",
        "no_upgrade-old_long_tag",
        "no_upgrade-new_long_tag",
        "no_upgrade-old_sha",
        "no_upgrade-new_sha",
        "no_upgrade-bad_tag",
        "no_upgrade-bad_sha",
        # upgrade
        "upgrade-old_short_tag",
        "upgrade-new_short_tag",
        "upgrade-old_long_tag",
        "upgrade-new_long_tag",
        "upgrade-old_sha",
        "upgrade-new_sha",
        "upgrade-bad_tag",
        "upgrade-bad_sha",
    ],
)
def test_update_line(
    upgrade: bool,
    curr_ref: str,
    exp_ref: str,
    exp_annotation: str | None,
    exp_skipped: bool,
) -> None:
    ctx = gha_harden.Context(upgrade, {}, Mock())

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
