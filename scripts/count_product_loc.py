#!/usr/bin/env python3
"""Подсчёт SLOC продуктового Python-кода (пакет threlium, без types/).

Учитываются только строки с исполняемым/объявляющим кодом:
исключаются пустые строки, комментарии (#) и docstring'и (AST).

По умолчанию корень:
  ansible/roles/threlium/files/scripts/threlium/
исключая каталог types/.

Примеры:
  python scripts/count_product_loc.py
  python scripts/count_product_loc.py --verbose
  python scripts/count_product_loc.py --with-types
"""
from __future__ import annotations

import argparse
import ast
import sys
import tokenize
from collections.abc import Iterable
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

_DOCSTRING_PARENT = (
    ast.Module,
    ast.ClassDef,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
)


@dataclass(frozen=True, slots=True)
class FileLoc:
    path: Path
    sloc: int
    physical: int
    blank: int
    comment_only: int
    docstring: int


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_product_root() -> Path:
    return _repo_root() / "ansible/roles/threlium/files/scripts/threlium"


def _iter_py_files(root: Path, *, include_types: bool) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        if not include_types:
            try:
                path.relative_to(root / "types")
            except ValueError:
                pass
            else:
                continue
        yield path


def _docstring_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, _DOCSTRING_PARENT):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if not isinstance(first, ast.Expr):
            continue
        val = first.value
        if not isinstance(val, ast.Constant) or not isinstance(val.value, str):
            continue
        end = first.end_lineno if first.end_lineno is not None else first.lineno
        lines.update(range(first.lineno, end + 1))
    return lines


def _blank_and_comment_only(source: str) -> tuple[set[int], set[int]]:
    lines = source.splitlines()
    n = len(lines)
    blank: set[int] = set()
    comment_only: set[int] = set()
    has_code: dict[int, bool] = dict.fromkeys(range(1, n + 1), False)

    try:
        tokens = tokenize.generate_tokens(StringIO(source).readline)
    except tokenize.TokenError:
        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                blank.add(i)
            elif stripped.startswith("#"):
                comment_only.add(i)
        return blank, comment_only

    for tok in tokens:
        ttype = tok.type
        if ttype in (tokenize.ENCODING, tokenize.ENDMARKER, tokenize.INDENT, tokenize.DEDENT):
            continue
        lineno = tok.start[0]
        if ttype == tokenize.COMMENT:
            if not has_code[lineno]:
                pass  # may become comment-only below
        elif ttype not in (tokenize.NL, tokenize.NEWLINE):
            has_code[lineno] = True

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            blank.add(i)
        elif stripped.startswith("#") and not has_code[i]:
            comment_only.add(i)

    return blank, comment_only


def count_file_sloc(path: Path) -> FileLoc:
    source = path.read_text(encoding="utf-8")
    physical = len(source.splitlines())
    tree = ast.parse(source, filename=str(path))
    doc_lines = _docstring_lines(tree)
    blank, comment_only = _blank_and_comment_only(source)

    excluded = blank | comment_only | doc_lines
    sloc = sum(1 for ln in range(1, physical + 1) if ln not in excluded)

    return FileLoc(
        path=path,
        sloc=sloc,
        physical=physical,
        blank=len(blank),
        comment_only=len(comment_only),
        docstring=len(doc_lines),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Корень пакета threlium (по умолчанию ansible/.../threlium)",
    )
    parser.add_argument(
        "--with-types",
        action="store_true",
        help="Включить каталог types/ в подсчёт",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Печатать SLOC по каждому файлу",
    )
    args = parser.parse_args(argv)

    root = (args.root or _default_product_root()).resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 1

    files = list(_iter_py_files(root, include_types=args.with_types))
    if not files:
        print(f"error: no .py files under {root}", file=sys.stderr)
        return 1

    per_file: list[FileLoc] = []
    errors: list[str] = []
    for path in files:
        try:
            per_file.append(count_file_sloc(path))
        except SyntaxError as exc:
            errors.append(f"{path}: {exc}")

    if errors:
        for msg in errors:
            print(msg, file=sys.stderr)
        return 1

    total_sloc = sum(f.sloc for f in per_file)
    rel_root = root
    try:
        rel_root = root.relative_to(_repo_root())
    except ValueError:
        pass

    scope = "threlium (включая types)" if args.with_types else "threlium без types/"
    print(f"Корень: {rel_root}")
    print(f"Область: {scope}")
    print(f"Файлов: {len(per_file)}")
    print(f"SLOC (код без пустых, # и docstring): {total_sloc}")

    if args.verbose:
        print()
        for entry in sorted(per_file, key=lambda e: (-e.sloc, str(e.path))):
            try:
                rel = entry.path.relative_to(_repo_root())
            except ValueError:
                rel = entry.path
            print(f"  {entry.sloc:5d}  {rel}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
