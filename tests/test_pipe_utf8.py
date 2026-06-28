from __future__ import annotations

from x4_copilot.pipe import NamedPipeServer


class FakeWin32File:
    def __init__(self) -> None:
        self.written: bytes | None = None

    def WriteFile(self, handle: object, data: bytes) -> None:  # noqa: N802 - mimics pywin32
        self.written = data

    def FlushFileBuffers(self, handle: object) -> None:  # noqa: N802 - mimics pywin32
        return None


class FakeWin32Pipe:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def PeekNamedPipe(self, handle: object, size: int) -> tuple[bytes, int, int]:  # noqa: N802 - mimics pywin32
        return b"", len(self.data), 0


class FakeReadFile(FakeWin32File):
    def __init__(self, data: bytes) -> None:
        super().__init__()
        self.data = data

    def ReadFile(self, handle: object, size: int) -> tuple[int, bytes]:  # noqa: N802 - mimics pywin32
        return 0, self.data


def test_named_pipe_server_writes_utf8_without_ascii_degrading() -> None:
    pipe = NamedPipeServer()
    fake_file = FakeWin32File()
    pipe._handle = object()
    pipe._win32file = fake_file

    pipe.write("Président you’re clear — go…")

    assert fake_file.written == "Président you’re clear — go…".encode()


def test_named_pipe_server_reads_utf8_without_ascii_degrading() -> None:
    text = "Président you’re clear — go…"
    pipe = NamedPipeServer()
    pipe._handle = object()
    pipe._win32file = FakeReadFile(text.encode("utf-8"))
    pipe._win32pipe = FakeWin32Pipe(text.encode("utf-8"))

    assert pipe.read() == text
