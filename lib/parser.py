"""Code file parsing: file-level summaries and symbol-level extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SymbolDoc:
    """A code symbol (function, class, interface, etc.)"""

    symbol: str
    symbol_type: str  # function, class, interface, type, enum, variable, export
    line_start: int
    line_end: int
    text: str


@dataclass
class FileDoc:
    """A source file summary."""

    file: str
    language: str
    line_count: int
    exports: list[str]
    text_preview: str
    symbols: list[SymbolDoc]


def _detect_language(path: str) -> str:
    ext = Path(path).suffix.lower()
    mapping = {
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".vue": "vue",
        ".py": "python",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".swift": "swift",
        ".rb": "ruby",
        ".php": "php",
    }
    return mapping.get(ext, "unknown")


# Regex patterns for symbol extraction (language-agnostic with TS/JS focus)
_PATTERNS = [
    # export function / const / let / var
    (re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)"), "function"),
    (re.compile(r"^\s*export\s+(?:const|let|var)\s+(\w+)\s*="), "variable"),
    # export class / interface / type / enum
    (re.compile(r"^\s*export\s+(?:default\s+)?class\s+(\w+)"), "class"),
    (re.compile(r"^\s*export\s+interface\s+(\w+)"), "interface"),
    (re.compile(r"^\s*export\s+type\s+(\w+)"), "type"),
    (re.compile(r"^\s*export\s+enum\s+(\w+)"), "enum"),
    # function declarations (non-export)
    (re.compile(r"^\s*(?:async\s+)?function\s+(\w+)"), "function"),
    # class declarations (non-export)
    (re.compile(r"^\s*class\s+(\w+)"), "class"),
    # Vue script setup defineProps / defineEmits / defineModel
    (re.compile(r"^\s*defineProps\s*[<\(]"), "vue_macro"),
    (re.compile(r"^\s*defineEmits\s*[<\(]"), "vue_macro"),
    (re.compile(r"^\s*defineModel\s*[<\(]"), "vue_macro"),
    # Go: func
    (re.compile(r"^\s*func\s+(?:\([^)]+\)\s+)?(\w+)"), "function"),
    # Go: type
    (re.compile(r"^\s*type\s+(\w+)"), "type"),
    # Python: def / class
    (re.compile(r"^\s*def\s+(\w+)"), "function"),
    (re.compile(r"^\s*class\s+(\w+)"), "class"),
    # Rust: fn / struct / enum / trait / impl
    (re.compile(r"^\s*fn\s+(\w+)"), "function"),
    (re.compile(r"^\s*struct\s+(\w+)"), "class"),
    (re.compile(r"^\s*enum\s+(\w+)"), "enum"),
    (re.compile(r"^\s*trait\s+(\w+)"), "interface"),
    # Java / Kotlin: method / class / interface
    (re.compile(r"^\s*(?:public|private|protected|internal)?\s*(?:static\s+)?(?:final\s+)?[\w<>,\s]+\s+(\w+)\s*\("), "function"),
    (re.compile(r"^\s*(?:public\s+)?class\s+(\w+)"), "class"),
    (re.compile(r"^\s*(?:public\s+)?interface\s+(\w+)"), "interface"),
]


def _extract_symbols(lines: list[str]) -> list[SymbolDoc]:
    """Extract top-level symbols from code lines."""
    symbols: list[SymbolDoc] = []
    for i, line in enumerate(lines):
        for pattern, sym_type in _PATTERNS:
            match = pattern.match(line)
            if match:
                name = match.group(1) if match.lastindex else line.strip()[:40]
                # Simple heuristic: symbol spans from this line until next top-level construct
                # or blank line followed by top-level
                end = i + 1
                indent = len(line) - len(line.lstrip())
                for j in range(i + 1, len(lines)):
                    next_line = lines[j]
                    if not next_line.strip():
                        continue
                    next_indent = len(next_line) - len(next_line.lstrip())
                    if next_indent <= indent and any(p.match(next_line) for p, _ in _PATTERNS):
                        end = j
                        break
                    end = j + 1
                text = "\n".join(lines[i:end]).strip()
                symbols.append(
                    SymbolDoc(
                        symbol=name,
                        symbol_type=sym_type,
                        line_start=i + 1,
                        line_end=end,
                        text=text,
                    )
                )
                break
    return symbols


def _extract_exports(lines: list[str]) -> list[str]:
    """Extract export names from a file."""
    exports: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("export "):
            for pattern, sym_type in _PATTERNS:
                match = pattern.match(line)
                if match and match.lastindex:
                    exports.append(match.group(1))
                    break
    return exports


def parse_file(file_path: str, max_lines: int = 200) -> FileDoc:
    """Parse a source file into a FileDoc."""
    path = Path(file_path)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw_lines = f.readlines()
    except Exception:
        raw_lines = []

    lines = [line.rstrip("\n\r") for line in raw_lines]
    line_count = len(lines)
    language = _detect_language(file_path)
    exports = _extract_exports(lines)

    # For file-level index, take first max_lines + exports list as preview
    preview_lines = lines[:max_lines]
    preview = "\n".join(preview_lines)
    if len(lines) > max_lines:
        preview += f"\n... ({len(lines) - max_lines} more lines)"

    # Extract symbols for optional symbol-level indexing
    symbols = _extract_symbols(lines)

    return FileDoc(
        file=file_path,
        language=language,
        line_count=line_count,
        exports=exports,
        text_preview=preview,
        symbols=symbols,
    )


def format_for_embedding(doc: FileDoc) -> str:
    """Format a FileDoc into a text string suitable for embedding."""
    parts = [
        f"File: {doc.file}",
        f"Language: {doc.language}",
    ]
    if doc.exports:
        parts.append(f"Exports: {', '.join(doc.exports[:20])}")
    parts.append(doc.text_preview)
    return "\n".join(parts)


def format_symbol_for_embedding(file_doc: FileDoc, sym: SymbolDoc) -> str:
    """Format a symbol for embedding."""
    parts = [
        f"File: {file_doc.file}",
        f"Symbol: {sym.symbol}",
        f"Type: {sym.symbol_type}",
        f"Lines: {sym.line_start}-{sym.line_end}",
        sym.text,
    ]
    return "\n".join(parts)
