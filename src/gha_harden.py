import json
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import click
from rich.console import Console, Group
from rich.live import Live
from rich.progress import BarColumn, Progress, TaskProgressColumn, TimeRemainingColumn
from rich.text import Text

SHA_RE = re.compile(r"[0-9a-f]{40}", re.IGNORECASE)
MAJOR_VERSION_RE = re.compile(r"v?\d+")
FULL_VERSION_RE = re.compile(r"^v?\d+\.\d+\.\d+")
USES_RE = re.compile(
    r"^(?P<prefix>[ \t]*-?[ \t]*uses:[ \t]*)"
    r"(?P<quote>['\"]?)"
    r"(?P<uses>[^'\"\s#]+)"
    r"(?P=quote)"
    r"(?P<suffix>[^\r\n]*)"
    r"(?P<newline>\r?\n?)$"
)


@dataclass
class Context:
    upgrade: bool
    pin: bool
    filter_set: set[str]
    exclude_set: set[str]
    skipped: dict[str, str]
    set_status: Callable[[str | None], None]


@dataclass(frozen=True)
class ActionUpdate:
    uses: str
    annotation: str | None


@dataclass(frozen=True)
class ActionSkip:
    uses: str
    reason: str


@click.command()
@click.option(
    "--upgrade",
    is_flag=True,
    help="Upgrade refs to the highest version tag",
)
@click.option(
    "--pin/--no-pin",
    default=True,
    help="Pin unpinned refs to commit SHAs. Existing SHA pins remain pinned.",
)
@click.option(
    "--filter",
    "-f",
    "filters",
    multiple=True,
    metavar="ORG[/REPO]",
    help="Only update matching owners or repositories. Can be repeated.",
)
@click.option(
    "--exclude",
    "-x",
    "excludes",
    multiple=True,
    metavar="ORG[/REPO]",
    help="Skip matching owners or repositories. Can be repeated; overrides --filter.",
)
@click.argument(
    "paths",
    nargs=-1,
    type=click.Path(
        exists=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        path_type=Path,
    ),
)
def main(
    upgrade: bool,
    pin: bool,
    filters: tuple[str, ...],
    excludes: tuple[str, ...],
    paths: tuple[Path, ...],
):
    """Harden GitHub Actions workflow references.

    Pinning is enabled by default; upgrading is opt-in.

    \b
    Options             Unpinned refs       SHA-pinned refs
    ------------------  ------------------  ------------------
    (default)           Pin current ref     Unchanged
    --no-pin            Unchanged           Unchanged
    --upgrade           Pin latest version  Pin latest version
    --upgrade --no-pin  Latest version tag  Pin latest version

    Note: with --upgrade --no-pin, major-only refs (vX or X) instead upgrade to
    the highest published major-only tag with the same prefix. If no newer tag
    exists, the ref is unchanged.

    Pinned refs are annotated with a version tag when available.

    Modifies the supplied files in place. Without paths, discovers YAML files
    under .github/workflows and .github/actions in the current Git repository.

    Use --filter and --exclude to select action owners or repositories.
    Matching is case-insensitive; excludes take precedence.
    """
    console = Console(highlight=False)
    filter_set = normalize_filters(filters)
    exclude_set = normalize_filters(excludes)

    try:
        user = gh_api("user")["login"]
    except subprocess.CalledProcessError:
        raise click.ClickException(
            "gh api failed, make sure you are logged in with `gh auth login`"
        )
    console.print(f"Logged in to GitHub as [bold cyan]{user}[/bold cyan]")
    console.print()

    if paths:
        root = Path.cwd()
        selected_paths = paths
    else:
        root = get_repo_root()
        selected_paths = get_workflow_paths(root)

    skipped: dict[str, str] = {}
    updated_files = 0
    updated_actions = 0

    progress = Progress(
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    )
    task = progress.add_task("", total=len(selected_paths))

    with Live(Group(render_status(), progress), console=console) as live:

        def set_status(detail: str | None = None) -> None:
            left = str(path.relative_to(root))
            live.update(Group(render_status(left=left, right=detail), progress))

        ctx = Context(
            upgrade=upgrade,
            pin=pin,
            filter_set=filter_set,
            exclude_set=exclude_set,
            skipped=skipped,
            set_status=set_status,
        )

        for path in selected_paths:
            set_status()
            changed = update_file(ctx, path)
            if changed:
                updated_files += 1
                updated_actions += changed
            progress.advance(task)

        live.update(Group(render_status(), progress))

    if skipped:
        console.print(f"[bold yellow]warn[/bold yellow] skipped {len(skipped)} action refs")
        for uses, reason in sorted(skipped.items()):
            console.print(f"  • {uses}: {reason}", markup=False)

    console.print()
    console.print(
        f"Updated [bold cyan]{updated_actions}[/bold cyan] actions "
        f"in [bold cyan]{updated_files}[/bold cyan] files"
    )


def normalize_filters(filters: Iterable[str]) -> set[str]:
    normalized = set()

    for f in filters:
        nf = f.strip().casefold()
        parts = nf.split("/")
        if len(parts) > 2 or any(part == "" for part in parts):
            raise click.ClickException(f"invalid filter {f!r}, expected ORG or ORG/REPO")
        normalized.add(nf)

    return normalized


def get_repo_root() -> Path:
    root = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], encoding="utf-8")
    return Path(root.strip())


def get_workflow_paths(root: Path) -> list[Path]:
    paths = []
    for base in [root / ".github" / "workflows", root / ".github" / "actions"]:
        paths.extend(base.rglob("*.yml"))
        paths.extend(base.rglob("*.yaml"))
    return sorted(paths)


def render_status(left: str | None = None, right: str | None = None) -> Text:
    if left is None:
        return Text("Checking GitHub Actions")

    text = Text("Checking ")
    text.append(left, style="green")
    if right:
        text.append(" ")
        text.append(right, style="cyan")
    return text


def update_file(ctx: Context, path: Path) -> int:
    updated_lines = []
    updated = 0

    with path.open(newline="") as source:
        for line in source:
            updated_line = update_line(ctx, line)
            updated_lines.append(updated_line)
            updated += updated_line != line

    if updated:
        with path.open("w", newline="") as destination:
            destination.write("".join(updated_lines))

    return updated


def update_line(ctx: Context, line: str) -> str:
    match = USES_RE.match(line)
    if match is None:
        return line

    original = match["uses"]
    if original.startswith(("./", "../", "docker://")) or "@" not in original:
        return line

    spec, ref = original.rsplit("@", maxsplit=1)
    repo = "/".join(spec.split("/")[:2])
    if not matches_filters(ctx, repo):
        return line

    # Special case: dtolnay/rust-toolchain uses tags to specify the toolchain
    # version. Unless the ref is "master" or "v1" we can't pin or upgrade this
    # without adding the `toolchain` input, so we skip it for now.
    if repo == "dtolnay/rust-toolchain" and not (
        ref in {"master", "v1"} or bool(SHA_RE.fullmatch(ref))
    ):
        return line

    ctx.set_status(f"{repo}@{ref}")
    try:
        result = update_action(ctx, spec, repo, ref)
    except subprocess.CalledProcessError as e:
        details = str(e.stderr) if e.stderr else str(e)
        print(f"GitHub API request failed for {spec}@{ref}: {details}")
        result = ActionSkip(uses=f"{spec}@{ref}", reason="GitHub API request failed")

    if result is None:
        return line
    elif isinstance(result, ActionSkip):
        ctx.skipped[result.uses] = result.reason
        return line

    ctx.set_status(result.uses)

    return (
        f"{match['prefix']}"
        f"{match['quote']}{result.uses}{match['quote']}"
        f"{updated_comment(match['suffix'], result.annotation)}"
        f"{match['newline']}"
    )


def matches_filters(ctx: Context, repo: str) -> bool:
    normalized_repo = repo.casefold()
    owner, _, _ = normalized_repo.partition("/")
    if owner in ctx.exclude_set or normalized_repo in ctx.exclude_set:
        return False
    return not ctx.filter_set or owner in ctx.filter_set or normalized_repo in ctx.filter_set


def updated_comment(suffix: str, annotation: str | None) -> str:
    before = suffix.partition("#")[0]
    if annotation is None:
        return before.rstrip()
    return f"{before or ' '}# {annotation}"


def update_action(ctx: Context, spec: str, repo: str, ref: str) -> ActionUpdate | ActionSkip | None:
    already_pinned = bool(SHA_RE.fullmatch(ref))
    default_reason = "No suitable version tag"

    if ctx.upgrade:
        if ctx.pin or already_pinned:
            # fetch latest tag and its sha, pin it and annotate with the tag
            target = get_latest_full_tag(repo)
            if target is None:
                return ActionSkip(uses=f"{spec}@{ref}", reason=default_reason)
            annotation, sha = target
            return ActionUpdate(uses=f"{spec}@{sha}", annotation=annotation)

        else:
            if MAJOR_VERSION_RE.fullmatch(ref):
                # just fetch the latest major version tag, no annotation
                tag = get_latest_major_tag(repo, ref)
                if tag is None:
                    return ActionSkip(uses=f"{spec}@{ref}", reason=default_reason)
                elif tag == ref:
                    return None
                return ActionUpdate(uses=f"{spec}@{tag}", annotation=None)

            # just fetch the latest tag, no annotation
            target = get_latest_full_tag(repo)
            if target is None:
                return ActionSkip(uses=f"{spec}@{ref}", reason=default_reason)
            tag, _ = target
            if tag == ref:
                return None
            return ActionUpdate(uses=f"{spec}@{tag}", annotation=None)

    else:
        if ctx.pin:
            sha = ref if already_pinned else resolve_ref_sha(repo, ref)
            annotation = resolve_sha_tag(repo, sha)
            return ActionUpdate(uses=f"{spec}@{sha}", annotation=annotation)

        else:
            if already_pinned:
                # just annotate
                annotation = resolve_sha_tag(repo, ref)
                return ActionUpdate(uses=f"{spec}@{ref}", annotation=annotation)
            else:
                return None


def get_latest_major_tag(repo: str, ref: str) -> str | None:
    def version_key(tag: dict) -> int:
        return int(tag["name"].removeprefix("v"))

    tags = (
        tag
        for tag in get_tags(repo)
        if MAJOR_VERSION_RE.fullmatch(tag["name"])
        and tag["name"].startswith("v") == ref.startswith("v")
    )

    for tag in sorted(tags, key=version_key, reverse=True):
        return tag["name"]

    return None


def get_latest_full_tag(repo: str) -> tuple[str, str] | None:
    def version_key(tag: dict) -> tuple[int, ...]:
        return tuple(map(int, tag["name"].removeprefix("v").split(".")))

    tags = (tag for tag in get_tags(repo) if FULL_VERSION_RE.fullmatch(tag["name"]))

    for tag in sorted(tags, key=version_key, reverse=True):
        return tag["name"], tag["commit"]["sha"]

    return None


def resolve_sha_tag(repo: str, sha: str) -> str | None:
    for tag in get_tags(repo):
        if FULL_VERSION_RE.fullmatch(tag["name"]) and tag["commit"]["sha"] == sha:
            return tag["name"]
    return None


def resolve_ref_sha(repo: str, ref: str) -> str:
    return get_commit_details(repo, ref)["sha"]


@cache
def get_tags(repo: str) -> list[dict]:
    pages = gh_api(f"repos/{repo}/tags?per_page=100", paginate=True)
    return [tag for page in pages for tag in page]


@cache
def get_commit_details(repo: str, ref: str) -> dict:
    return gh_api(f"repos/{repo}/commits/{ref}")


def gh_api(path: str, *, paginate: bool = False) -> Any:
    command = ["gh", "api", path]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    output = subprocess.check_output(
        command,
        encoding="utf-8",
        stderr=subprocess.DEVNULL,
    )
    return json.loads(output)
