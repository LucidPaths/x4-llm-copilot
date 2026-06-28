from __future__ import annotations

import sys
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

PIPE_DISCONNECTED_CODES = {109, 232, 233}
PIPE_BUSY_CODES = {231}


class PipeDisconnectedError(ConnectionError):
    """Raised when the X4 side closes a named-pipe session."""


class PipeBusyError(ConnectionError):
    """Raised when an old named-pipe instance is still occupied."""


def _pipe_error_code(exc: BaseException) -> int | None:
    code = getattr(exc, "winerror", None)
    if isinstance(code, int):
        return code
    if exc.args and isinstance(exc.args[0], int):
        return exc.args[0]
    return None


class DuplexTransport(Protocol):
    def connect(self) -> None: ...
    def read(self) -> str: ...
    def write(self, message: str) -> None: ...
    def close(self) -> None: ...


@dataclass
class NamedPipeServer:
    """Windows named-pipe server compatible with SirNukes' X4 client.

    A pipe handle is a single session. After X4 save/reload/UI reload, close this handle and
    call ``connect()`` again so the server creates a fresh named-pipe instance for X4 to attach.
    """

    pipe_name: str = "x4_llm_copilot"
    buffer_size: int = 64 * 1024
    timeout_s: float | None = None

    def __post_init__(self) -> None:
        self._handle = None
        self._win32file = None
        self._win32pipe = None

    @property
    def pipe_path(self) -> str:
        return rf"\\.\pipe\{self.pipe_name}"

    def connect(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("NamedPipeServer requires Windows/pywin32; use stdio/samples for non-game tests")
        try:
            import win32file  # type: ignore[import-not-found]
            import win32pipe  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("pywin32 is required: uv pip install -e '.[winpipe]'") from exc
        self.close()
        self._win32file = win32file
        self._win32pipe = win32pipe
        try:
            self._handle = win32pipe.CreateNamedPipe(
                self.pipe_path,
                win32pipe.PIPE_ACCESS_DUPLEX,
                win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
                1,
                self.buffer_size,
                self.buffer_size,
                0,
                None,
            )
        except Exception as exc:
            if _pipe_error_code(exc) in PIPE_BUSY_CODES:
                raise PipeBusyError("named pipe instance is busy") from exc
            raise
        win32pipe.ConnectNamedPipe(self._handle, None)

    def read(self) -> str:
        if self._handle is None or self._win32file is None:
            raise RuntimeError("pipe is not connected")
        try:
            _err, data = self._win32file.ReadFile(self._handle, self.buffer_size)
        except Exception as exc:
            if _pipe_error_code(exc) in PIPE_DISCONNECTED_CODES:
                raise PipeDisconnectedError("named pipe client disconnected") from exc
            raise
        return data.decode("utf-8")

    def write(self, message: str) -> None:
        if self._handle is None or self._win32file is None:
            raise RuntimeError("pipe is not connected")
        try:
            self._win32file.WriteFile(self._handle, message.encode("utf-8"))
        except Exception as exc:
            if _pipe_error_code(exc) in PIPE_DISCONNECTED_CODES:
                raise PipeDisconnectedError("named pipe client disconnected") from exc
            raise

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            if self._win32pipe is not None:
                with suppress(Exception):
                    self._win32pipe.DisconnectNamedPipe(self._handle)
            if self._win32file is not None:
                self._win32file.CloseHandle(self._handle)
        finally:
            self._handle = None
