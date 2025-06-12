import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self

from joblib import Parallel, delayed
from pathspec import PathSpec
from pathspec.patterns.gitwildmatch import GitWildMatchPattern

log = logging.getLogger(__name__)


class LineType(StrEnum):
    """Enum for different types of lines in search results."""

    MATCH = "match"
    """Part of the matched lines"""
    BEFORE_MATCH = "prefix"
    """Lines before the match"""
    AFTER_MATCH = "postfix"
    """Lines after the match"""


@dataclass(kw_only=True)
class TextLine:
    """Represents a line of text with information on how it relates to the match."""

    line_number: int
    line_content: str
    match_type: LineType
    """Represents the type of line (match, prefix, postfix)"""

    def get_display_prefix(self) -> str:
        """Get the display prefix for this line based on the match type."""
        if self.match_type == LineType.MATCH:
            return "  >"
        return "..."

    def format_line(self, include_line_numbers: bool = True) -> str:
        """Format the line for display (e.g.,for logging or passing to an LLM).

        :param include_line_numbers: Whether to include the line number in the result.
        """
        prefix = self.get_display_prefix()
        if include_line_numbers:
            line_num = str(self.line_number).rjust(4)
            prefix = f"{prefix}{line_num}"
        return f"{prefix}:{self.line_content}"


@dataclass(kw_only=True)
class MatchedConsecutiveLines:
    """Represents a collection of consecutive lines found through some criterion in a text file or a string.
    May include lines before, after, and matched.
    """

    lines: list[TextLine]
    """All lines in the context of the match. At least one of them is of `match_type` `MATCH`."""
    source_file_path: str | None = None
    """Path to the file where the match was found (Metadata)."""

    # set in post-init
    lines_before_matched: list[TextLine] = field(default_factory=list)
    matched_lines: list[TextLine] = field(default_factory=list)
    lines_after_matched: list[TextLine] = field(default_factory=list)

    def __post_init__(self) -> None:
        for line in self.lines:
            if line.match_type == LineType.BEFORE_MATCH:
                self.lines_before_matched.append(line)
            elif line.match_type == LineType.MATCH:
                self.matched_lines.append(line)
            elif line.match_type == LineType.AFTER_MATCH:
                self.lines_after_matched.append(line)

        assert len(self.matched_lines) > 0, "At least one matched line is required"

    @property
    def start_line(self) -> int:
        return self.lines[0].line_number

    @property
    def end_line(self) -> int:
        return self.lines[-1].line_number

    @property
    def num_matched_lines(self) -> int:
        return len(self.matched_lines)

    def to_display_string(self, include_line_numbers: bool = True) -> str:
        return "\n".join([line.format_line(include_line_numbers) for line in self.lines])

    @classmethod
    def from_file_contents(
        cls, file_contents: str, line: int, context_lines_before: int = 0, context_lines_after: int = 0, source_file_path: str | None = None
    ) -> Self:
        line_contents = file_contents.split("\n")
        start_lineno = max(0, line - context_lines_before)
        end_lineno = min(len(line_contents) - 1, line + context_lines_after)
        text_lines: list[TextLine] = []
        # before the line
        for lineno in range(start_lineno, line):
            text_lines.append(TextLine(line_number=lineno, line_content=line_contents[lineno], match_type=LineType.BEFORE_MATCH))
        # the line
        text_lines.append(TextLine(line_number=line, line_content=line_contents[line], match_type=LineType.MATCH))
        # after the line
        for lineno in range(line + 1, end_lineno + 1):
            text_lines.append(TextLine(line_number=lineno, line_content=line_contents[lineno], match_type=LineType.AFTER_MATCH))

        return cls(lines=text_lines, source_file_path=source_file_path)


def search_text(
    pattern: str | re.Pattern[str],
    content: str | None = None,
    source_file_path: str | None = None,
    allow_multiline_match: bool = False,
    context_lines_before: int = 0,
    context_lines_after: int = 0,
    is_glob: bool = False,
) -> list[MatchedConsecutiveLines]:
    """
    Search for a pattern in text content. Supports both regex and glob-like patterns.

    Args:
        pattern: Pattern to search for (regex or glob-like pattern)
        content: The text content to search. May be None if source_file_path is provided.
        source_file_path: Optional path to the source file. If content is None,
            this has to be passed and the file will be read.
        allow_multiline_match: Whether to search across multiple lines. Currently, the default
            option (False) is very inefficient, so it is recommended to set this to True.
        context_lines_before: Number of context lines to include before matches
        context_lines_after: Number of context lines to include after matches
        is_glob: If True, pattern is treated as a glob-like pattern (e.g., "*.py", "test_??.py")
                 and will be converted to regex internally

    Returns:
        List of TextSearchMatch objects

    :raises: ValueError if the pattern is not valid

    """
    if source_file_path and content is None:
        with open(source_file_path) as f:
            content = f.read()

    if content is None:
        raise ValueError("Pass either content or source_file_path")

    matches = []

    # Convert pattern to a compiled regex if it's a string
    if is_glob and isinstance(pattern, str):
        # Convert glob pattern to regex
        # Escape all regex special characters except * and ?
        regex_special_chars = r"\^$.|+()[{"
        escaped_pattern = ""
        for char in pattern:
            if char in regex_special_chars:
                escaped_pattern += "\\" + char
            elif char == "*":
                escaped_pattern += ".*"
            elif char == "?":
                escaped_pattern += "."
            else:
                escaped_pattern += char
        # For glob patterns, don't anchor with ^ and $ to allow partial line matches
        compiled_pattern = re.compile(escaped_pattern)
    elif isinstance(pattern, str):
        try:
            compiled_pattern = re.compile(pattern)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}") from e
    else:
        # Pattern is already a compiled regex
        compiled_pattern = pattern

    # Split the content into lines for processing
    lines = content.splitlines()
    total_lines = len(lines)

    if allow_multiline_match:
        # For multiline matches, we need to use the DOTALL flag to make '.' match newlines
        if isinstance(pattern, str):
            # If we've compiled the pattern ourselves, we need to recompile with DOTALL
            pattern_str = compiled_pattern.pattern
            compiled_pattern = re.compile(pattern_str, re.DOTALL)
        # Search across the entire content as a single string
        for match in compiled_pattern.finditer(content):
            start_pos = match.start()
            end_pos = match.end()

            # Find the line numbers for the start and end positions
            start_line_num = content[:start_pos].count("\n") + 1
            end_line_num = content[:end_pos].count("\n") + 1

            # Calculate the range of lines to include in the context
            context_start = max(1, start_line_num - context_lines_before)
            context_end = min(total_lines, end_line_num + context_lines_after)

            # Create TextLine objects for the context
            context_lines = []
            for i in range(context_start - 1, context_end):
                line_num = i + 1
                if context_start <= line_num < start_line_num:
                    match_type = LineType.BEFORE_MATCH
                elif end_line_num < line_num <= context_end:
                    match_type = LineType.AFTER_MATCH
                else:
                    match_type = LineType.MATCH

                context_lines.append(TextLine(line_number=line_num, line_content=lines[i], match_type=match_type))

            matches.append(MatchedConsecutiveLines(lines=context_lines, source_file_path=source_file_path))
    else:
        # TODO: extremely inefficient! Since we currently don't use this option in SerenaAgent or LanguageServer,
        #   it is not urgent to fix, but should be either improved or the option should be removed.
        # Search line by line
        for i, line in enumerate(lines):
            line_num = i + 1
            if compiled_pattern.search(line):
                # Calculate the range of lines to include in the context
                context_start = max(0, i - context_lines_before)
                context_end = min(total_lines - 1, i + context_lines_after)

                # Create TextLine objects for the context
                context_lines = []
                for j in range(context_start, context_end + 1):
                    context_line_num = j + 1
                    if j < i:
                        match_type = LineType.BEFORE_MATCH
                    elif j > i:
                        match_type = LineType.AFTER_MATCH
                    else:
                        match_type = LineType.MATCH

                    context_lines.append(TextLine(line_number=context_line_num, line_content=lines[j], match_type=match_type))

                matches.append(MatchedConsecutiveLines(lines=context_lines, source_file_path=source_file_path))

    return matches


def default_file_reader(file_path: str) -> str:
    """Reads using utf-8 encoding."""
    with open(file_path, encoding="utf-8") as f:
        return f.read()


def search_files(
    file_paths: list[str],
    pattern: re.Pattern | str,
    file_reader: Callable[[str], str] = default_file_reader,
    context_lines_before: int = 0,
    context_lines_after: int = 0,
    paths_include_glob: str | None = None,
    paths_exclude_glob: str | None = None,
) -> list[MatchedConsecutiveLines]:
    """
    Search for a pattern in a list of files.

    :param file_paths: List of files in which to search
    :param pattern: Pattern to search for
    :param file_reader: Function to read a file, by default will just use os.open.
        All files that can't be read by it will be skipped.
    :param context_lines_before: Number of context lines to include before matches
    :param context_lines_after: Number of context lines to include after matches
    :param paths_include_glob: Optional glob pattern to include files from the list
    :param paths_exclude_glob: Optional glob pattern to exclude files from the list
    :return: List of MatchedConsecutiveLines objects
    """
    # Pre-filter paths (done sequentially to avoid overhead)
    include_spec = PathSpec.from_lines(GitWildMatchPattern, [paths_include_glob]) if paths_include_glob else None
    exclude_spec = PathSpec.from_lines(GitWildMatchPattern, [paths_exclude_glob]) if paths_exclude_glob else None

    filtered_paths = []
    for path in file_paths:
        if include_spec and not include_spec.match_file(path):
            log.debug(f"Skipping {path}: does not match include pattern {paths_include_glob}")
            continue
        if exclude_spec and exclude_spec.match_file(path):
            log.debug(f"Skipping {path}: matches exclude pattern {paths_exclude_glob}")
            continue
        filtered_paths.append(path)

    log.info(f"Processing {len(filtered_paths)} files.")

    def process_single_file(path: str) -> dict[str, Any]:
        """Process a single file - this function will be parallelized."""
        try:
            file_content = file_reader(path)
            search_results = search_text(
                pattern,
                content=file_content,
                source_file_path=path,
                allow_multiline_match=True,
                context_lines_before=context_lines_before,
                context_lines_after=context_lines_after,
            )
            if len(search_results) > 0:
                log.debug(f"Found {len(search_results)} matches in {path}")
            return {"path": path, "results": search_results, "error": None}
        except Exception as e:
            log.debug(f"Error processing {path}: {e}")
            return {"path": path, "results": [], "error": str(e)}

    # Execute in parallel using joblib
    results = Parallel(
        n_jobs=-1,
        backend="threading",
    )(delayed(process_single_file)(path) for path in filtered_paths)

    # Collect results and errors
    matches = []
    skipped_file_error_tuples = []

    for result in results:
        if result["error"]:
            skipped_file_error_tuples.append((result["path"], result["error"]))
        else:
            matches.extend(result["results"])

    if skipped_file_error_tuples:
        log.debug(f"Failed to read {len(skipped_file_error_tuples)} files: {skipped_file_error_tuples}")

    log.info(f"Found {len(matches)} total matches across {len(filtered_paths)} files")
    return matches
