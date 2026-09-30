import json
import re
import subprocess
from collections.abc import Callable
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
def main():
    """Harden GitHub Actions workflow references.

    Pinned refs are annotated with a version tag when available.

    Discovers and modifies YAML files under .github/workflows and .github/actions
    in the current Git repository.
    """
    console = Console(highlight=False)

    try:
        user = gh_api("user")["login"]
    except subprocess.CalledProcessError:
        raise click.ClickException(
            "gh api failed, make sure you are logged in with `gh auth login`"
        )
    console.print(f"Logged in to GitHub as [bold cyan]{user}[/bold cyan]")
    console.print()

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


def updated_comment(suffix: str, annotation: str | None) -> str:
    before = suffix.partition("#")[0]
    if annotation is None:
        return before.rstrip()
    return f"{before or ' '}# {annotation}"


def update_action(ctx: Context, spec: str, repo: str, ref: str) -> ActionUpdate | ActionSkip | None:
    already_pinned = bool(SHA_RE.fullmatch(ref))
    sha = ref if already_pinned else resolve_ref_sha(repo, ref)
    annotation = resolve_sha_tag(repo, sha)
    return ActionUpdate(uses=f"{spec}@{sha}", annotation=annotation)


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
