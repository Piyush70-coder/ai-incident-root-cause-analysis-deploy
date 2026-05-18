"""
Log processing service to extract and process log file contents with PII Masking.
"""
import os
import zipfile
import tarfile
import gzip
import io
import re
from typing import Optional, Tuple, Union
from .pii_masker import mask_pii  #

MAX_PROCESS_LINES = 5000
SMART_CONTEXT_MAX_CHARS = 20000
SMART_CONTEXT_FALLBACK_CHARS = 2000
IMPORTANT_LOG_PATTERN = re.compile(
    r"\b(ERROR|EXCEPTION|FATAL|TRACE|TRACEBACK|CRITICAL|WARN)\b",
    re.IGNORECASE,
)


def _read_limited_lines(stream, max_lines: int = MAX_PROCESS_LINES) -> Tuple[str, bool]:
    """
    Read up to max_lines from a text stream.
    Returns (content, truncated_flag).
    """
    lines = []
    truncated = False
    for index, line in enumerate(stream):
        if index >= max_lines:
            truncated = True
            break
        lines.append(line)
    return "".join(lines), truncated


def extract_log_content(log_file, max_lines: int = MAX_PROCESS_LINES) -> Tuple[str, bool]:
    """
    Extract text content from log file.
    Supports: .txt, .log, .zip, .tar.gz, .gz
    Applies line-level truncation for large files.
    """
    content = ""
    truncated = False
    file_path = log_file.path if hasattr(log_file, 'path') else None

    # If it's a file path, read from disk
    if file_path and os.path.exists(file_path):
        file_ext = os.path.splitext(file_path)[1].lower()

        try:
            if file_ext in ['.zip']:
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    file_list = zip_ref.namelist()
                    if file_list:
                        with zip_ref.open(file_list[0], 'r') as zipped_file:
                            with io.TextIOWrapper(zipped_file, encoding='utf-8', errors='ignore') as text_stream:
                                content, truncated = _read_limited_lines(text_stream, max_lines=max_lines)

            elif file_ext in ['.gz', '.tar.gz']:
                if file_path.endswith('.tar.gz'):
                    with tarfile.open(file_path, 'r:gz') as tar:
                        members = tar.getmembers()
                        if members:
                            file_obj = tar.extractfile(members[0])
                            if file_obj:
                                with io.TextIOWrapper(file_obj, encoding='utf-8', errors='ignore') as text_stream:
                                    content, truncated = _read_limited_lines(text_stream, max_lines=max_lines)
                else:
                    with gzip.open(file_path, 'rt', encoding='utf-8', errors='ignore') as f:
                        content, truncated = _read_limited_lines(f, max_lines=max_lines)
            else:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content, truncated = _read_limited_lines(f, max_lines=max_lines)
        except Exception as e:
            try:
                with open(file_path, 'rb') as f:
                    with io.TextIOWrapper(f, encoding='utf-8', errors='ignore') as text_stream:
                        content, truncated = _read_limited_lines(text_stream, max_lines=max_lines)
            except:
                content = f"Error reading log file: {str(e)}"
                truncated = False

    elif hasattr(log_file, 'read'):
        try:
            log_file.seek(0)
            text_stream = io.TextIOWrapper(log_file, encoding='utf-8', errors='ignore')
            content, truncated = _read_limited_lines(text_stream, max_lines=max_lines)
            text_stream.detach()
        except Exception as e:
            content = f"Error reading log: {str(e)}"
            truncated = False

    return content, truncated


def process_log_file(incident_log, return_metadata: bool = False) -> Union[str, dict]:
    """
    Extracts content, masks PII, and finds key errors.
    """
    raw_content = getattr(incident_log, "raw_content", "") or ""
    truncated = False
    if not raw_content:
        raw_content, truncated = extract_log_content(incident_log.file)

    if not raw_content:
        if return_metadata:
            return {"processed_content": "", "truncated": truncated}
        return ""

    # 2. SABSE IMPORTANT: Pehle Masking karo (Phase 1)
    # Isse security ensure hogi aur sensitive data AI tak nahi jayega
    safe_content = mask_pii(raw_content)

    # 3. Masked content se errors/warnings nikaalo
    lines = safe_content.split('\n')
    error_lines = []
    warning_lines = []

    for line in lines:
        line_lower = line.lower()
        if any(keyword in line_lower for keyword in ['error', 'exception', 'failed', 'failure', 'fatal']):
            error_lines.append(line[:200])  # Truncate long lines
        elif any(keyword in line_lower for keyword in ['warn', 'warning', 'critical']):
            warning_lines.append(line[:200])

    # 4. Final summary build karo (Pehal 5000 chars masked hone chahiye)
    processed = safe_content[:5000]

    if error_lines:
        processed += "\n\n=== KEY ERRORS (MASKED) ===\n" + "\n".join(error_lines[:20])

    if warning_lines:
        processed += "\n\n=== KEY WARNINGS (MASKED) ===\n" + "\n".join(warning_lines[:10])

    if truncated:
        processed += "\n\n[TRUNCATED] Processed first 5000 lines only."

    if return_metadata:
        return {"processed_content": processed, "truncated": truncated}

    return processed


def extract_key_errors(log_content: str, max_errors: int = 10) -> list:
    """
    Extract key error messages from already masked content.
    """
    # Defense-in-depth: sanitize again in case caller passes raw content.
    log_content = mask_pii(log_content)
    errors = []
    lines = log_content.split('\n')

    for line in lines:
        line_lower = line.lower()
        if any(keyword in line_lower for keyword in ['error', 'exception', 'failed', 'failure', 'fatal', 'timeout']):
            clean_line = line.strip()[:300]
            if clean_line and clean_line not in errors:
                errors.append(clean_line)
                if len(errors) >= max_errors:
                    break
    return errors


def extract_smart_context(log_text: str) -> str:
    """
    Reduce large logs to only relevant error context for LLM input.

    Rules:
    - Match important lines by keywords.
    - Keep +/- 3 lines around each match.
    - Deduplicate overlapping windows while preserving order.
    - Hard limit output to 20,000 characters.
    - If no matches, return first 2,000 characters.
    """
    if not log_text:
        return ""

    lines = log_text.splitlines()
    if not lines:
        return ""

    matched_indexes = [
        idx for idx, line in enumerate(lines) if IMPORTANT_LOG_PATTERN.search(line)
    ]

    if not matched_indexes:
        return log_text[:SMART_CONTEXT_FALLBACK_CHARS]

    selected_indexes = set()
    last_index = len(lines) - 1
    for idx in matched_indexes:
        start = max(0, idx - 3)
        end = min(last_index, idx + 3)
        selected_indexes.update(range(start, end + 1))

    ordered_lines = [lines[idx] for idx in sorted(selected_indexes)]
    filtered = "\n".join(ordered_lines)

    if len(filtered) > SMART_CONTEXT_MAX_CHARS:
        return filtered[:SMART_CONTEXT_MAX_CHARS]
    return filtered
