"""Pack-owned, isolated dependencies prepared by OAW's plugin installation.

The host's site-packages are never modified. A -I -S worker uses this private
dependency directory and the host's standard library (or a managed Python 3.12
when the host is older). Relative receipts also survive desktop relocation.
This module deliberately uses only the standard library.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import time
from uuid import uuid4

PACKAGE = Path(__file__).resolve().parent
REQUIREMENTS = PACKAGE / "native-requirements.txt"
ENGINE = PACKAGE.parent / "oaw_matcreator_engine"
REPAIR = "MatCreator plugin setup is incomplete. Use the same Python for OAW setup and startup."


def environment_root() -> Path:
    # ABI changes require fresh wheels. Patch upgrades can reuse the same files.
    identity = b"oaw-matcreator-dependencies-v2\n" + REQUIREMENTS.read_bytes()
    # Use the interpreter's build target; platform.machine() depends on mutable
    # Windows environment variables and can be empty in isolated launchers.
    identity += f"{sys.implementation.cache_tag}:{sysconfig.get_platform()}".encode()
    return Path(sys.prefix) / "oaw-runtimes" / "matcreator" / hashlib.sha256(identity).hexdigest()[:20]


def read_environment(root: Path | None = None) -> tuple[str, Path]:
    root = root or environment_root()
    try:
        receipt = json.loads((root / "ready.json").read_text(encoding="utf-8"))
        if receipt["format"] != 2:
            raise ValueError("Unknown runtime receipt")
        relative = receipt["python"]
        executable = Path(sys.executable) if relative == "host" else root / relative
        if relative != "host" and (Path(relative).is_absolute() or not executable.resolve().is_relative_to(root.resolve())):
            raise ValueError("Runtime interpreter escapes installation")
        if not executable.is_file() or not all((root / "packages" / module / "__init__.py").is_file()
                for module in ("google/adk", "litellm", "json_repair")):
            raise ValueError("Incomplete runtime")
        return str(executable), root
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(
            f"{REPAIR} Backend Python: {sys.executable}. Expected runtime: {root}. "
            f"Reason: {exc}"
        ) from exc


def activate(root: Path) -> None:
    # Called only in the isolated worker or install-time verifier, never the host.
    import site
    site.addsitedir(str(root / "packages"))
    # LiteLLM owns a bundled cache and otherwise overrides TIKTOKEN_CACHE_DIR.
    # Pin both variables so ambient settings cannot redirect the managed worker.
    cache = str(root / "packages" / "litellm" / "litellm_core_utils" / "tokenizers")
    os.environ["TIKTOKEN_CACHE_DIR"] = cache
    os.environ["CUSTOM_TIKTOKEN_CACHE_DIR"] = cache
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"


def activate_engine() -> None:
    """Load maintained Pack source, independent of dependency installation/cache."""
    if sys.version_info < (3, 12):
        raise RuntimeError("MatCreator requires Python 3.12+. " + REPAIR)
    if not (ENGINE / "agent.py").is_file():
        raise RuntimeError("The MatCreator Pack is missing its bundled engine.")
    sys.path.insert(0, str(PACKAGE.parent))
    import importlib.util
    spec = importlib.util.find_spec("oaw_matcreator_engine")
    if spec is None or Path(spec.origin).resolve() != (ENGINE / "__init__.py").resolve():
        raise RuntimeError("MatCreator must load the engine shipped in this Pack.")


@contextmanager
def installation_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "install.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + 1800
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeError("Another MatCreator plugin installation is still running.") from exc
                time.sleep(.2)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def run(arguments, *, env):
    subprocess.run([str(arg) for arg in arguments], check=True, env=env,
                   stdin=subprocess.DEVNULL, timeout=1800,
                   **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))


def install() -> None:
    root = environment_root()
    with installation_lock(root):
        try:
            executable, _ = read_environment(root)
            run([executable, "-I", "-S", Path(__file__), "--check", root], env=os.environ.copy())
            print("MatCreator native runtime is ready.", flush=True)
            return
        except (RuntimeError, OSError, subprocess.SubprocessError):
            pass
        # Never publish a failed/partial install as ready. Preserve failed files
        # for diagnosis; the next normal setup retries in a fresh directory.
        (root / "ready.json").unlink(missing_ok=True)
        packages = root / "packages"
        if packages.exists():
            packages.rename(root / ("packages.incomplete-" + uuid4().hex[:8]))
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("OAW plugin setup requires uv on PATH.")
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON") and k.upper() not in {"VIRTUAL_ENV", "CONDA_PREFIX"}}
        env.update(PYTHONUTF8="1", LITELLM_LOCAL_MODEL_COST_MAP="True")
        executable = Path(sys.executable)
        relative = "host"
        if sys.version_info < (3, 12):
            env["UV_PYTHON_INSTALL_DIR"] = str(root / "python")
            print("Preparing MatCreator Python 3.12...", flush=True)
            run([uv, "python", "install", "3.12"], env=env)
            executable = Path(subprocess.check_output([uv, "python", "find", "--managed-python", "3.12"],
                              env=env, text=True, timeout=60).strip()).resolve()
            relative = executable.relative_to(root.resolve()).as_posix()
        print("Installing MatCreator orchestration dependencies...", flush=True)
        run([uv, "pip", "install", "--python", executable, "--target", root / "packages",
             "-r", REQUIREMENTS], env=env)
        print("Verifying MatCreator and preparing tokenizers...", flush=True)
        run([executable, "-I", "-S", Path(__file__), "--check", root], env=env)
        temporary = root / "ready.tmp"
        temporary.write_text(json.dumps({"format": 2, "python": relative}), encoding="utf-8")
        temporary.replace(root / "ready.json")
        print("MatCreator native runtime is ready.", flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--check":
        activate(Path(sys.argv[2]))
        activate_engine()
        import litellm  # noqa: F401
        from google.adk.runners import Runner  # noqa: F401
        import tiktoken
        tiktoken.get_encoding("cl100k_base")
        tiktoken.get_encoding("o200k_base")
    else:
        install()
