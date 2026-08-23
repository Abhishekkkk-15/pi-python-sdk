import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
import httpx


SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
    ".pi-python",
    ".memory",
    ".cursor",
}

SKIP_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
}


async def _kill_process_tree(pid: int):
    """Terminates a process tree cleanly cross-platform."""
    try:
        if sys.platform == "win32":
            await asyncio.to_thread(
                subprocess.run,
                f"taskkill /F /T /PID {pid}",
                shell=True,
                capture_output=True,
            )
        else:
            await asyncio.to_thread(os.kill, pid, 9)
    except Exception:
        pass


DEFAULT_MAX_LINES = 1000
DEFAULT_MAX_BYTES = 50 * 1024  # 50KB


async def execute_read(
    path: str, offset: Optional[int] = None, limit: Optional[int] = None
) -> str:
    """Reads and returns the contents of a text file, supporting offset and limit, with line formatting and truncation limits."""
    try:
        filepath = Path(path)
        if not filepath.exists():
            return f"Error: File '{path}' does not exist."
        if not filepath.is_file():
            return f"Error: '{path}' is a directory, not a file."

        try:
            async with aiofiles.open(filepath, "r", encoding="utf-8") as f:
                content = await f.read()
        except UnicodeDecodeError:
            return f"Error: File '{path}' appears to be binary and not decodable as UTF-8 text."

        all_lines = content.splitlines()
        total_lines = len(all_lines)

        # Convert from 1-indexed input to 0-indexed array access
        start_line = max(0, (offset - 1) if offset else 0)
        if start_line >= total_lines and total_lines > 0:
            return f"Error: Offset {offset} is beyond end of file ({total_lines} lines total)"

        if limit is not None:
            end_line = min(start_line + limit, total_lines)
            selected_lines = all_lines[start_line:end_line]
        else:
            selected_lines = all_lines[start_line:]

        # Truncate content if it exceeds line or byte limits
        truncated_lines = []
        bytes_accumulated = 0
        truncated = False
        truncated_by = None

        for idx, line in enumerate(selected_lines):
            formatted_line = f"{start_line + idx + 1}: {line}"
            line_bytes = len(formatted_line.encode("utf-8")) + 1  # +1 for newline

            if len(truncated_lines) >= DEFAULT_MAX_LINES:
                truncated = True
                truncated_by = "lines"
                break

            if bytes_accumulated + line_bytes > DEFAULT_MAX_BYTES:
                truncated = True
                truncated_by = "bytes"
                break

            truncated_lines.append(formatted_line)
            bytes_accumulated += line_bytes

        output_text = "\n".join(truncated_lines)
        output_lines_count = len(truncated_lines)
        end_line_display = start_line + output_lines_count

        if truncated:
            next_offset = end_line_display + 1
            if truncated_by == "lines":
                output_text += f"\n\n[Truncated: showing {output_lines_count} lines of {total_lines}. Use offset={next_offset} to continue.]"
            else:
                output_text += f"\n\n[Truncated: showing {output_lines_count} lines of {total_lines} ({DEFAULT_MAX_BYTES // 1024}KB limit). Use offset={next_offset} to continue.]"
        elif limit is not None and start_line + limit < total_lines:
            remaining = total_lines - (start_line + limit)
            next_offset = start_line + limit + 1
            output_text += f"\n\n[{remaining} more lines in file. Use offset={next_offset} to continue.]"

        return output_text
    except Exception as e:
        return f"Error reading file '{path}': {str(e)}"


async def execute_write(path: str, content: str) -> str:
    """Creates or completely overwrites a file with the given content."""
    try:
        filepath = Path(path)
        existed = filepath.exists() and filepath.is_file()
        # Create parent directories if they don't exist
        filepath.parent.mkdir(parents=True, exist_ok=True)
        text = content or ""
        async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
            await f.write(text)
        lines = len(text.splitlines()) if text else 0
        nbytes = len(text.encode("utf-8"))
        action = "Overwrote" if existed else "Created"
        return f"{action} '{path}' - {lines} lines, {nbytes} bytes."
    except Exception as e:
        return f"Error writing file '{path}': {str(e)}"


async def execute_edit(path: str, edits: List[Dict[str, str]]) -> str:
    """
    Applies parallel exact search-and-replace edits to a file.
    All edits are matched against the original file state before any modifications occur.
    """
    try:
        filepath = Path(path)
        if not filepath.exists():
            return f"Error: File '{path}' does not exist."

        async with aiofiles.open(filepath, "r", encoding="utf-8") as f:
            content = await f.read()
        # 1. Normalize CRLF to LF for matching consistency
        normalized_content = content.replace("\r\n", "\n")

        matches = []
        for i, edit in enumerate(edits):
            old_text = (edit.get("oldText", "") or "").replace("\r\n", "\n")
            new_text = (edit.get("newText", "") or "").replace("\r\n", "\n")

            if not old_text:
                return f"Error in edit {i + 1}: 'oldText' cannot be empty."

            # Check uniqueness in original content
            occurrences = normalized_content.count(old_text)
            if occurrences == 0:
                return (
                    f"Error in edit entry {i + 1}: Could not find exact match for 'oldText'.\n"
                    f"Target text was:\n{old_text}"
                )
            if occurrences > 1:
                return (
                    f"Error in edit entry {i + 1}: 'oldText' matched {occurrences} locations. "
                    "Provide more surrounding context to make it unique."
                )

            start_idx = normalized_content.index(old_text)
            end_idx = start_idx + len(old_text)
            matches.append({
                "index": i,
                "start": start_idx,
                "end": end_idx,
                "new_text": new_text,
                "old_text": old_text,
            })

        # 2. Check for overlapping edit regions
        matches.sort(key=lambda m: m["start"])
        for i in range(1, len(matches)):
            prev = matches[i - 1]
            curr = matches[i]
            if prev["end"] > curr["start"]:
                return (
                    f"Error: Edits {prev['index'] + 1} and {curr['index'] + 1} overlap in '{path}'. "
                    "Merge them into a single replacement block."
                )

        # 3. Apply edits in reverse order (highest start index down to 0)
        new_content = normalized_content
        for m in reversed(matches):
            new_content = new_content[:m["start"]] + m["new_text"] + new_content[m["end"]:]

        # Restore CRLF line endings if original file used CRLF
        if "\r\n" in content:
            new_content = new_content.replace("\n", "\r\n")

        async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
            await f.write(new_content)
        return f"Applied {len(edits)} edit(s) to '{path}' successfully."

    except Exception as e:
        return f"Error editing file '{path}': {str(e)}"


MAX_BASH_LINES = 2000
MAX_BASH_BYTES = 50 * 1024  # 50KB


def _truncate_bash_output(output: str) -> str:
    """
    Truncates command output if it exceeds line or byte limits.
    Preserves initial output head (first 500 lines) and failure tail (last 1,500 lines)
    so exit status and error tracebacks remain visible to the model.
    """
    text = output.strip()
    if not text:
        return text

    lines = text.splitlines()
    total_lines = len(lines)
    raw_bytes = len(text.encode("utf-8"))

    if total_lines <= MAX_BASH_LINES and raw_bytes <= MAX_BASH_BYTES:
        return text

    head_count = 500
    tail_count = 1500

    if total_lines > (head_count + tail_count):
        head = lines[:head_count]
        tail = lines[-tail_count:]
        omitted = total_lines - (head_count + tail_count)
        notice = f"\n\n[... Output truncated: {omitted:,} lines ({raw_bytes / 1024:.1f} KB) omitted to stay under 50KB limit ...]\n\n"
        return "\n".join(head) + notice + "\n".join(tail)

    if raw_bytes > MAX_BASH_BYTES:
        head_bytes = 15 * 1024
        tail_bytes = 35 * 1024
        encoded = text.encode("utf-8")
        head_part = encoded[:head_bytes].decode("utf-8", errors="ignore")
        tail_part = encoded[-tail_bytes:].decode("utf-8", errors="ignore")
        notice = f"\n\n[... Output truncated: {raw_bytes / 1024:.1f} KB exceeded 50KB limit ...]\n\n"
        return head_part + notice + tail_part

    return text


def _format_bash_error(exc: BaseException) -> str:
    msg = str(exc).strip()
    if msg:
        return f"Error executing command: {msg}"
    return f"Error executing command: {type(exc).__name__}"


def _resolve_windows_bash() -> Optional[Path]:
    """Prefer Git Bash on Windows for Unix-style commands (pwd, ls, etc.)."""
    for key in ("PI_SDK_BASH", "BASH"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            path = Path(raw)
            if path.is_file():
                return path

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates = [
        Path(program_files) / "Git" / "bin" / "bash.exe",
        Path(program_files_x86) / "Git" / "bin" / "bash.exe",
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _build_bash_env() -> dict[str, str]:
    env = os.environ.copy()
    env["CI"] = "true"
    env["DEBIAN_FRONTEND"] = "noninteractive"
    env["npm_config_yes"] = "true"
    env["NONINTERACTIVE"] = "1"
    env["FORCE_COLOR"] = "0"
    env["NO_COLOR"] = "1"
    env["PIP_NO_INPUT"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_bash_blocking(
    command: str,
    timeout: int,
    should_run_bg: bool,
    env: dict[str, str],
    creationflags: int,
) -> str:
    """Run bash via subprocess.run/Popen (works on any asyncio event loop)."""
    bash = _resolve_windows_bash() if sys.platform == "win32" else None
    use_shell = bash is None
    popen_args: Any = command
    run_args: Any = command
    if bash is not None:
        popen_args = [str(bash), "-lc", command]
        run_args = popen_args

    wait_limit = 4 if should_run_bg else timeout

    if should_run_bg:
        proc = subprocess.Popen(
            popen_args,
            shell=use_shell,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=creationflags,
        )
        try:
            stdout, stderr = proc.communicate(timeout=wait_limit)
        except subprocess.TimeoutExpired:
            output = (stdout or "") + (stderr or "")
            output_str = _truncate_bash_output(output) if output else "[Process started successfully]"
            return (
                f"{output_str}\n\n"
                f"[Background process started and running with PID {proc.pid}]"
            )
        output = (stdout or "") + (stderr or "")
        if not output.strip():
            return "[Command finished with no output]"
        return _truncate_bash_output(output)

    try:
        completed = subprocess.run(
            run_args,
            shell=use_shell,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=wait_limit,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        return f"[Error: Command timed out after {timeout} seconds and was terminated.]"

    output = (completed.stdout or "") + (completed.stderr or "")
    if not output.strip():
        return "[Command finished with no output]"
    return _truncate_bash_output(output)


async def _execute_subprocess_argv(
    argv: List[str],
    timeout: int,
    should_run_bg: bool,
    env: dict[str, str],
    creationflags: int,
) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        creationflags=creationflags,
    )

    stdout_lines: List[str] = []
    stderr_lines: List[str] = []

    async def _read_stream(stream, output_list):
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                output_list.append(line.decode("utf-8", errors="replace"))
        except Exception:
            pass

    read_stdout = asyncio.create_task(_read_stream(proc.stdout, stdout_lines))
    read_stderr = asyncio.create_task(_read_stream(proc.stderr, stderr_lines))

    wait_limit = 4 if should_run_bg else timeout

    try:
        await asyncio.wait_for(proc.wait(), timeout=wait_limit)
    except asyncio.TimeoutError:
        pass

    is_running = proc.returncode is None

    if is_running:
        if should_run_bg:
            output = "".join(stdout_lines) + "".join(stderr_lines)
            output_str = _truncate_bash_output(output) if output else "[Process started successfully]"
            return (
                f"{output_str}\n\n"
                f"[Background process started and running with PID {proc.pid}]"
            )
        output = "".join(stdout_lines) + "".join(stderr_lines)
        output_str = _truncate_bash_output(output) if output else "[No output received before timeout]"
        await _kill_process_tree(proc.pid)
        await asyncio.gather(read_stdout, read_stderr, return_exceptions=True)
        return (
            f"{output_str}\n\n"
            f"[Error: Command timed out after {timeout} seconds and was terminated.]"
        )

    await asyncio.gather(read_stdout, read_stderr, return_exceptions=True)
    output = "".join(stdout_lines) + "".join(stderr_lines)
    if not output.strip():
        return "[Command finished with no output]"
    return _truncate_bash_output(output)


def _run_subprocess_argv_blocking(
    argv: List[str],
    timeout: int,
    should_run_bg: bool,
    env: dict[str, str],
    creationflags: int,
) -> str:
    wait_limit = 4 if should_run_bg else timeout

    if should_run_bg:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=creationflags,
        )
        try:
            stdout, stderr = proc.communicate(timeout=wait_limit)
        except subprocess.TimeoutExpired:
            output = (stdout or "") + (stderr or "")
            output_str = _truncate_bash_output(output) if output else "[Process started successfully]"
            return (
                f"{output_str}\n\n"
                f"[Background process started and running with PID {proc.pid}]"
            )
        output = (stdout or "") + (stderr or "")
        if not output.strip():
            return "[Command finished with no output]"
        return _truncate_bash_output(output)

    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=wait_limit,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        return f"[Error: Command timed out after {timeout} seconds and was terminated.]"

    output = (completed.stdout or "") + (completed.stderr or "")
    if not output.strip():
        return "[Command finished with no output]"
    return _truncate_bash_output(output)


async def _execute_bash_async(
    command: str,
    timeout: int,
    should_run_bg: bool,
    env: dict[str, str],
    creationflags: int,
) -> str:
    bash = _resolve_windows_bash() if sys.platform == "win32" else None

    if bash is not None:
        return await _execute_subprocess_argv(
            [str(bash), "-lc", command],
            timeout,
            should_run_bg,
            env,
            creationflags,
        )

    proc = await asyncio.create_subprocess_shell(
        command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        creationflags=creationflags,
    )

    stdout_lines: List[str] = []
    stderr_lines: List[str] = []

    async def _read_stream(stream, output_list):
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                output_list.append(line.decode("utf-8", errors="replace"))
        except Exception:
            pass

    read_stdout = asyncio.create_task(_read_stream(proc.stdout, stdout_lines))
    read_stderr = asyncio.create_task(_read_stream(proc.stderr, stderr_lines))

    wait_limit = 4 if should_run_bg else timeout

    try:
        await asyncio.wait_for(proc.wait(), timeout=wait_limit)
    except asyncio.TimeoutError:
        pass

    is_running = proc.returncode is None

    if is_running:
        if should_run_bg:
            output = "".join(stdout_lines) + "".join(stderr_lines)
            output_str = _truncate_bash_output(output) if output else "[Process started successfully]"
            return (
                f"{output_str}\n\n"
                f"[Background process started and running with PID {proc.pid}]"
            )
        output = "".join(stdout_lines) + "".join(stderr_lines)
        output_str = _truncate_bash_output(output) if output else "[No output received before timeout]"
        await _kill_process_tree(proc.pid)
        await asyncio.gather(read_stdout, read_stderr, return_exceptions=True)
        return (
            f"{output_str}\n\n"
            f"[Error: Command timed out after {timeout} seconds and was terminated.]"
        )

    await asyncio.gather(read_stdout, read_stderr, return_exceptions=True)
    output = "".join(stdout_lines) + "".join(stderr_lines)
    if not output.strip():
        return "[Command finished with no output]"
    return _truncate_bash_output(output)


async def execute_bash(
    command: str, timeout: int = 30, is_background: bool = False
) -> str:
    """
    Executes a terminal command cross-platform without hanging or freezing.
    Auto-detects or handles long-running server commands (e.g. npm run dev, vite),
    prevents interactive CLI prompt hangs using stdin=DEVNULL,
    and kills processes cleanly on timeout to prevent lingering process leaks.

    On Windows, prefers Git Bash (``bash.exe``) when installed so Unix commands
    like ``pwd`` and ``ls`` work during local development. Set ``PI_SDK_BASH`` to
    override the bash path. Falls back to ``cmd.exe`` via ``asyncio.to_thread``
    when the event loop does not support async subprocess (common on Windows).
    """
    should_run_bg = _should_run_background(command, is_background)

    env = _build_bash_env()
    creationflags = 0
    if sys.platform == "win32" and should_run_bg:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        return await _execute_bash_async(
            command, timeout, should_run_bg, env, creationflags
        )
    except NotImplementedError:
        # Windows SelectorEventLoop (e.g. some uvicorn setups) — sync fallback.
        try:
            return await asyncio.to_thread(
                _run_bash_blocking,
                command,
                timeout,
                should_run_bg,
                env,
                creationflags,
            )
        except Exception as exc:
            return _format_bash_error(exc)
    except Exception as e:
        return _format_bash_error(e)


_BASH_BG_KEYWORDS = [
    "npm run dev", "npm start", "vite", "next dev", "ng serve",
    "gatsby develop", "nodemon", "uvicorn", "gunicorn", "flask run",
    "python -m http.server",
]


def _should_run_background(command: str, is_background: bool) -> bool:
    command_lower = command.lower()
    auto_bg = any(kw in command_lower for kw in _BASH_BG_KEYWORDS)
    return is_background or auto_bg


def _resolve_docker_container(
    container: Optional[str] = None,
    default_container: Optional[str] = None,
) -> Optional[str]:
    raw = (container or "").strip()
    if raw:
        return raw
    default = (default_container or "").strip()
    if default:
        return default
    for key in ("PI_SDK_DOCKER_CONTAINER", "DOCKER_CONTAINER"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return None


def _build_docker_exec_argv(
    command: str,
    container: str,
    workdir: Optional[str] = None,
    user: Optional[str] = None,
) -> List[str]:
    argv = ["docker", "exec", "-i"]
    wd = (workdir or "").strip()
    if wd:
        argv.extend(["-w", wd])
    u = (user or "").strip()
    if u:
        argv.extend(["-u", u])
    argv.extend([container, "bash", "-lc", command])
    return argv


async def execute_docker_bash(
    command: str,
    container: Optional[str] = None,
    workdir: Optional[str] = None,
    user: Optional[str] = None,
    timeout: int = 30,
    is_background: bool = False,
    default_container: Optional[str] = None,
) -> str:
    """
    Executes a bash command inside a running Docker container via ``docker exec``.

    Container resolution order: ``container`` argument, then ``default_container``
    (typically from ``Agent.create(docker_container=...)``), then
    ``PI_SDK_DOCKER_CONTAINER``, then ``DOCKER_CONTAINER``.
    Requires Docker CLI on the host and a running container.
    """
    resolved_container = _resolve_docker_container(
        container, default_container=default_container
    )
    if not resolved_container:
        return (
            "Error: Docker container not specified. "
            "Pass container=, set Agent.create(docker_container=...), "
            "or set PI_SDK_DOCKER_CONTAINER / DOCKER_CONTAINER."
        )

    should_run_bg = _should_run_background(command, is_background)
    env = _build_bash_env()
    creationflags = 0
    if sys.platform == "win32" and should_run_bg:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    argv = _build_docker_exec_argv(
        command,
        resolved_container,
        workdir=workdir,
        user=user,
    )

    try:
        return await _execute_subprocess_argv(
            argv, timeout, should_run_bg, env, creationflags
        )
    except NotImplementedError:
        try:
            return await asyncio.to_thread(
                _run_subprocess_argv_blocking,
                argv,
                timeout,
                should_run_bg,
                env,
                creationflags,
            )
        except Exception as exc:
            return _format_bash_error(exc)
    except Exception as e:
        return _format_bash_error(e)


async def execute_web_search(query: str, max_results: int = 5) -> str:
    """Executes a real-time web search using the Tavily API."""
    from pi_sdk.config import get_tavily_api_key

    api_key = get_tavily_api_key()
    if not api_key:
        return (
            "Error: Tavily API key is not set. "
            "Pass tavily_api_key= to Agent.create, or set TAVILY_API_KEY."
        )

    try:
        try:
            from tavily import TavilyClient

            client = TavilyClient(api_key=api_key)
            response = await asyncio.to_thread(
                client.search, query=query, max_results=max_results
            )
            results = response.get("results", [])
        except ImportError:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": api_key,
                        "query": query,
                        "max_results": max_results,
                    },
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])

        if not results:
            return f"No web search results found for query: '{query}'."

        formatted_results = []
        for i, res in enumerate(results, 1):
            title = res.get("title", "No Title")
            url = res.get("url", "")
            content = res.get("content", "")
            formatted_results.append(f"[{i}] {title}\nURL: {url}\nContent: {content}\n")

        return "\n".join(formatted_results)

    except Exception as e:
        return f"Error executing web search: {str(e)}"


async def execute_grep(
    pattern: str,
    path: str = ".",
    glob: str = "",
    case_insensitive: bool = False,
    max_results: int = 50,
) -> str:
    """
    Search file contents for a regex/text pattern (cross-platform).
    Returns matching lines as path:line:content.
    """
    if not pattern:
        return "Error: pattern is required."

    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"Error: invalid regex pattern: {e}"

    root = Path(path or ".").expanduser()
    if not root.exists():
        return f"Error: path '{path}' does not exist."

    matches: List[str] = []
    files_searched = 0
    truncated = False
    max_results = max(1, min(int(max_results or 50), 200))

    def _should_skip_dir(name: str) -> bool:
        return name in SKIP_DIR_NAMES or name.startswith(".")

    def _iter_files() -> Any:
        if root.is_file():
            yield root
            return
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
            for filename in filenames:
                yield Path(dirpath) / filename

    try:
        for filepath in _iter_files():
            if truncated:
                break
            if not filepath.is_file():
                continue
            if filepath.suffix.lower() in SKIP_FILE_SUFFIXES:
                continue
            if glob:
                try:
                    if not filepath.match(glob) and not Path(filepath.name).match(glob):
                        continue
                except Exception:
                    continue

            files_searched += 1
            try:
                async with aiofiles.open(
                    filepath, "r", encoding="utf-8", errors="ignore"
                ) as f:
                    text = await f.read()
            except OSError:
                continue

            for line_no, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    rel = filepath
                    try:
                        rel = filepath.resolve().relative_to(Path.cwd().resolve())
                    except Exception:
                        rel = filepath
                    matches.append(f"{rel}:{line_no}:{line.rstrip()}")
                    if len(matches) >= max_results:
                        truncated = True
                        break

        if not matches:
            scope = f" in '{path}'" if path and path != "." else ""
            g = f" (glob={glob})" if glob else ""
            return f"No matches for /{pattern}/{scope}{g}. Searched {files_searched} file(s)."

        header = f"Found {len(matches)} match(es) in {files_searched} file(s)"
        if truncated:
            header += f" (truncated at {max_results})"
        return header + ":\n" + "\n".join(matches)
    except Exception as e:
        return f"Error executing grep: {e}"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read file contents at the given path. Supports optional offset and limit for paginated reading of large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative or absolute file path to read."
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (1-indexed, optional)."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read (optional)."
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Create a new file or completely overwrite an existing file with new content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to write to."
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete text content to write into the file."
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "Make precise, surgical changes to a file by providing exact original text blocks to replace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to edit."
                    },
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "oldText": {
                                    "type": "string",
                                    "description": "Exact text block from the file to be replaced."
                                },
                                "newText": {
                                    "type": "string",
                                    "description": "New text to replace oldText with."
                                }
                            },
                            "required": ["oldText", "newText"]
                        },
                        "description": "List of precise edits to perform."
                    }
                },
                "required": ["path", "edits"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run shell/bash commands on the host (ls, git, pytest, grep, find, npm, etc.). Non-blocking for background/dev server commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Bash command string to execute."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Maximum time in seconds to wait for command completion (default: 30)."
                    },
                    "is_background": {
                        "type": "boolean",
                        "description": "Set to true for long-running background tasks or dev servers (e.g., npm run dev)."
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "docker_bash",
            "description": (
                "Run shell/bash commands inside a running Docker container via docker exec. "
                "Use for project commands that must run in the container environment (pytest, npm, migrations, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Bash command string to execute inside the container."
                    },
                    "container": {
                        "type": "string",
                        "description": (
                            "Docker container name or ID. "
                            "Defaults to Agent.create(docker_container=...), then "
                            "PI_SDK_DOCKER_CONTAINER or DOCKER_CONTAINER env var."
                        )
                    },
                    "workdir": {
                        "type": "string",
                        "description": "Working directory inside the container (docker exec -w)."
                    },
                    "user": {
                        "type": "string",
                        "description": "User to run as inside the container (docker exec -u)."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Maximum time in seconds to wait for command completion (default: 30)."
                    },
                    "is_background": {
                        "type": "boolean",
                        "description": "Set to true for long-running background tasks or dev servers."
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Perform real-time web searches using Tavily for up-to-date documentation, news, or answers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string."
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of search results to return (default: 5)."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Search the workspace for a text/regex pattern across files. "
                "Prefer this over bash grep/findstr for code discovery."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex or plain text pattern to search for."
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search (default: current workspace)."
                    },
                    "glob": {
                        "type": "string",
                        "description": "Optional filename glob filter, e.g. '*.py' or '*.ts'."
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "Case-insensitive search (default: false)."
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum matches to return (default: 50, max: 200)."
                    }
                },
                "required": ["pattern"]
            }
        }
    }
]
