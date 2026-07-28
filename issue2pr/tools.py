from __future__ import annotations

import ast
import fnmatch
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Iterable, cast

from .models import AgentAction, ToolResult
from .safety import is_protected_path, resolve_inside
from .sandbox import CommandRunner

_EXCLUDED_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    "node_modules",
    ".issue2pr",
}
_SOURCE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".php"}


class ToolBox:
    def __init__(
        self,
        root: Path,
        test_command: tuple[str, ...],
        timeout: float = 300.0,
        runner: CommandRunner | None = None,
    ) -> None:
        self.root = root.resolve()
        self.test_command = test_command
        self.timeout = timeout
        self.runner = runner or CommandRunner()

    def execute(self, action: AgentAction) -> ToolResult:
        try:
            handler = cast(
                Callable[..., ToolResult] | None,
                {
                    "read_file": self.read_file,
                    "write_file": self.write_file,
                    "edit_file": self.edit_file,
                    "search": self.search,
                    "repo_map": self.repo_map,
                    "run_tests": self.run_tests,
                    "git": self.git,
                }.get(action.action),
            )
            if handler is None:
                return ToolResult(False, f"unknown tool: {action.action}")
            return handler(**action.args)
        except TypeError as exc:
            return ToolResult(False, f"invalid arguments for {action.action}: {exc}")
        except (ValueError, OSError) as exc:
            return ToolResult(False, f"{type(exc).__name__}: {exc}")

    def read_file(self, path: str) -> ToolResult:
        target = resolve_inside(self.root, path)
        if not target.is_file():
            return ToolResult(False, f"file not found: {path}")
        data = target.read_text(encoding="utf-8", errors="replace")
        return ToolResult(True, data[:40_960])

    def write_file(self, path: str, content: str) -> ToolResult:
        if is_protected_path(path):
            return ToolResult(False, "writes under .github/workflows are forbidden")
        if len(content.encode("utf-8")) > 200 * 1024:
            return ToolResult(False, "content exceeds 200 KiB")
        target = resolve_inside(self.root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(True, f"wrote {path} ({len(content)} chars)")

    def edit_file(self, path: str, old: str, new: str) -> ToolResult:
        if is_protected_path(path):
            return ToolResult(False, "writes under .github/workflows are forbidden")
        target = resolve_inside(self.root, path)
        if not target.is_file():
            return ToolResult(False, f"file not found: {path}")
        text = target.read_text(encoding="utf-8")
        occurrences = text.count(old)
        if occurrences != 1:
            return ToolResult(
                False,
                f"edit anchor must occur exactly once, found {occurrences}",
            )
        replacement = text.replace(old, new, 1)
        if len(replacement.encode("utf-8")) > 200 * 1024:
            return ToolResult(False, "file would exceed 200 KiB")
        target.write_text(replacement, encoding="utf-8")
        return ToolResult(True, f"edited {path}")

    def search(self, pattern: str, glob: str | None = None) -> ToolResult:
        rg = shutil.which("rg", path=self.runner.env.get("PATH", ""))
        if rg:
            argv = ["rg", "--line-number", "--no-heading", "--color", "never"]
            if glob:
                argv.extend(["--glob", glob])
            argv.extend(["--", pattern])
            result = self.runner.run(argv, self.root, 20)
            if not result.ok and result.returncode not in (0, 1):
                return ToolResult(False, result.output)
            return ToolResult(True, _cap_lines(result.output))
        return self._python_search(pattern, glob)

    def _python_search(self, pattern: str, glob: str | None) -> ToolResult:
        try:
            expression = re.compile(pattern)
        except re.error as exc:
            return ToolResult(False, f"invalid regular expression: {exc}")
        lines: list[str] = []
        for path in self._files():
            if glob and not fnmatch.fnmatch(str(path.relative_to(self.root)), glob):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if expression.search(line):
                    lines.append(f"{path.relative_to(self.root)}:{number}:{line}")
                    if len(lines) >= 100:
                        break
            if len(lines) >= 100:
                break
        return ToolResult(True, "\n".join(lines)[:8192])

    def repo_map(self) -> ToolResult:
        entries: list[str] = []
        for path in self._files():
            if len(entries) >= 200:
                break
            rel = path.relative_to(self.root).as_posix()
            if path.suffix == ".py":
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
                except (SyntaxError, ValueError, OSError):
                    entries.append(f"{rel}: <unparseable>")
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                        entries.append(f"{rel}:{node.lineno}: {prefix} {node.name}({ast.unparse(node.args)})")
                    elif isinstance(node, ast.ClassDef):
                        entries.append(f"{rel}:{node.lineno}: class {node.name}")
                    if len(entries) >= 200:
                        break
            elif path.suffix in _SOURCE_SUFFIXES:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for number, line in enumerate(text.splitlines(), start=1):
                    stripped = line.strip()
                    if re.match(
                        r"(export\s+)?(async\s+)?(function|class|def|fn|func|pub\s+fn)\b",
                        stripped,
                    ) or re.match(r"(export\s+)?(const|let)\s+\w+\s*=", stripped):
                        entries.append(f"{rel}:{number}: {stripped[:160]}")
                        if len(entries) >= 200:
                            break
        return ToolResult(True, "\n".join(entries)[:40_960])

    def run_tests(self) -> ToolResult:
        result = self.runner.run(list(self.test_command), self.root, self.timeout)
        label = "tests passed" if result.ok else f"tests failed (exit {result.returncode})"
        return ToolResult(result.ok, f"{label}\n{result.output}")

    def git(self, argv: list[str]) -> ToolResult:
        if not argv:
            return ToolResult(False, "git requires a subcommand")
        forbidden = {"push", "reset", "checkout", "switch", "clean", "rebase", "merge"}
        lowered = [str(arg).lower() for arg in argv]
        if lowered[0] in forbidden or "--force" in lowered or "-f" in lowered:
            return ToolResult(False, "git subcommand is not allowed")
        if lowered[0] not in {"status", "diff", "log"}:
            return ToolResult(False, "git subcommand is not allowed")
        if any(
            ".." in arg or arg.startswith("/") or arg in {"--no-index", "--"}
            for arg in lowered[1:]
        ):
            return ToolResult(False, "git path arguments are not allowed")
        result = self.runner.run(["git", *argv], self.root, 30)
        return ToolResult(result.ok, result.output)

    def _files(self) -> Iterable[Path]:
        files: list[Path] = []
        for path in self.root.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                relative = path.relative_to(self.root)
            except ValueError:
                continue
            if any(part in _EXCLUDED_PARTS for part in relative.parts):
                continue
            files.append(path)
        return sorted(files)


def _cap_lines(text: str, limit: int = 100) -> str:
    lines = text.splitlines()[:limit]
    return "\n".join(lines)[:8192]
