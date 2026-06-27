from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Protocol


class DuplexTransport(Protocol):
    def connect(self) -> None: ...
    def read(self) -> str: ...
    def write(self, message: str) -> None: ...
    def close(self) -> None: ...


@dataclass
class NamedPipeServer:
    """Windows named-pipe server compatible with SirNukes' X4 client."""

    pipe_name: str = "x4_llm_copilot"
    buffer_size: int = 64 * 1024

    def __post_init__(self) -> None:
        self._handle = None

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
        self._win32file = win32file
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
        win32pipe.ConnectNamedPipe(self._handle, None)

    def read(self) -> str:
        if self._handle is None:
            raise RuntimeError("pipe is not connected")
        _err, data = self._win32file.ReadFile(self._handle, self.buffer_size)
        return data.decode("utf-8")

    def write(self, message: str) -> None:
        if self._handle is None:
            raise RuntimeError("pipe is not connected")
        self._win32file.WriteFile(self._handle, message.encode("utf-8"))

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            self._win32file.CloseHandle(self._handle)
        finally:
            self._handle = None
