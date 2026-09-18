"""Optional local pi-ai sidecar lifecycle management."""

from __future__ import annotations

import os
import select
import shlex
import subprocess
import time
from typing import Iterable, Optional, Union


class SidecarStartupError(RuntimeError):
    pass


class SidecarProcess:
    """Manage an optional sidecar, or represent an externally managed URL."""

    def __init__(
        self,
        url: Optional[str] = None,
        command: Optional[Union[str, Iterable[str]]] = None,
        timeout: float = 5.0,
        stop_timeout: float = 2.0,
        env: Optional[dict[str, str]] = None,
    ) -> None:
        self.url = url.rstrip("/") if url else None
        self.command = command
        self.timeout = timeout
        self.stop_timeout = stop_timeout
        self.env = env
        self.process: Optional[subprocess.Popen[str]] = None

    @property
    def configured(self) -> bool:
        return bool(self.url or self.command)

    def start(self) -> Optional[str]:
        if self.url:
            return self.url
        if not self.command:
            return None
        command = shlex.split(self.command) if isinstance(self.command, str) else list(self.command)
        if not command:
            raise SidecarStartupError("empty pi sidecar command")
        child_env = os.environ.copy()
        if self.env:
            child_env.update(self.env)
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=child_env,
        )
        deadline = time.monotonic() + self.timeout
        assert self.process.stdout is not None
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            ready, _, _ = select.select([self.process.stdout], [], [], min(0.2, remaining))
            if not ready:
                if self.process.poll() is not None:
                    break
                continue
            line = self.process.stdout.readline().strip()
            if line.startswith("READY "):
                self.url = "http://" + line[6:].strip()
                return self.url
        process = self.process
        self.stop()
        details = ""
        if process is not None and process.stderr is not None:
            try:
                details = process.stderr.read(4096).strip()
            except OSError:
                pass
        raise SidecarStartupError(f"pi sidecar did not become ready{': ' + details if details else ''}")

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=self.stop_timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=self.stop_timeout)
        self.process = None
