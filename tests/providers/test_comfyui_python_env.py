"""ComfyUI's writable Python environment, end to end against a real pip (#3132).

An "image" venv with one installed package stands in for the runner image's
``/opt/venv``. The packaged script runs under its interpreter, as the img
container's start command does, and pip then runs offline against wheels built
here.
"""

from __future__ import annotations

import importlib.resources
import shutil
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

_SCRIPT = "hal0_python_env.py"


def _script_bytes() -> bytes:
    return importlib.resources.files("hal0.comfyui.assets").joinpath(_SCRIPT).read_bytes()


def _site(env: Path) -> Path:
    return (
        env / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    )


def _version_dir(root: Path) -> Path:
    return root / f"{sys.version_info.major}.{sys.version_info.minor}"


def _dist_info(name: str, version: str) -> str:
    return f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"


def _install_into_image(image: Path, name: str, version: str) -> None:
    """Lay a package down the way pip would have when the image was built."""
    site = _site(image)
    (site / f"{name}.py").write_text(f"VERSION = {version!r}\n")
    info = site / f"{name}-{version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(_dist_info(name, version))
    (info / "INSTALLER").write_text("pip\n")
    (info / "RECORD").write_text(f"{name}.py,,\n")


def _wheel(wheels: Path, name: str, version: str, *requires: str) -> None:
    """A minimal pure-python wheel, so no test reaches an index."""
    wheels.mkdir(exist_ok=True)
    info = f"{name}-{version}.dist-info"
    metadata = _dist_info(name, version) + "".join(f"Requires-Dist: {r}\n" for r in requires)
    with zipfile.ZipFile(wheels / f"{name}-{version}-py3-none-any.whl", "w") as whl:
        whl.writestr(f"{name}.py", f"VERSION = {version!r}\n")
        whl.writestr(f"{info}/METADATA", metadata)
        whl.writestr(
            f"{info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: hal0-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        whl.writestr(f"{info}/RECORD", "")


def _tree(path: Path) -> list[str]:
    """The relative paths under ``path``, bytecode caches aside."""
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if "__pycache__" not in p.parts)


def _run(python: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(python), *args], capture_output=True, text=True, check=False, timeout=120
    )


@pytest.fixture(scope="module")
def image(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The stand-in for ``/opt/venv``: a venv holding pip and two packages."""
    env = tmp_path_factory.mktemp("image") / "venv"
    venv.EnvBuilder(with_pip=True).create(env)
    _install_into_image(env, "imgdep", "1.0")
    _install_into_image(env, "torch", "2.9.1+rocm7")
    return env


@pytest.fixture(scope="module")
def prepared(image: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One prepared environment, shared: creating it runs ensurepip, which takes seconds."""
    root = tmp_path_factory.mktemp("data") / "python"
    root.mkdir()
    (root / _SCRIPT).write_bytes(_script_bytes())
    done = _run(image / "bin" / "python", str(root / _SCRIPT))
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    return root


def test_the_environment_sees_the_images_packages_and_is_the_interpreter(
    prepared: Path, image: Path
) -> None:
    python = prepared / "current" / "bin" / "python"
    done = _run(
        python, "-c", "import imgdep, os, sys; print(imgdep.VERSION, os.path.realpath(sys.prefix))"
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["1.0", str(_version_dir(prepared))]


def test_a_node_install_lands_in_the_environment_not_the_image(
    prepared: Path, image: Path, tmp_path: Path
) -> None:
    """``[sys.executable, "-m", "pip", "install", ...]`` is how the failing nodes ask."""
    wheels = tmp_path / "wheels"
    _wheel(wheels, "nodedep", "1.0")
    before = _tree(image)
    done = _run(
        prepared / "current" / "bin" / "python",
        *("-m", "pip", "install", "--no-index", "--find-links", str(wheels), "nodedep"),
    )
    assert done.returncode == 0, done.stderr
    assert (_site(_version_dir(prepared)) / "nodedep.py").is_file()
    assert _tree(image) == before


def test_a_dependency_the_image_satisfies_is_not_installed_again(
    prepared: Path, image: Path, tmp_path: Path
) -> None:
    """The reason this is not ``PIP_TARGET``: ``--target`` ignores what is installed, so a
    node needing ``accelerate`` would pull a second torch from PyPI over the ROCm one."""
    wheels = tmp_path / "wheels"
    _wheel(wheels, "needsimg", "1.0", "imgdep>=1.0")
    done = _run(
        prepared / "current" / "bin" / "python",
        *("-m", "pip", "install", "--no-index", "--find-links", str(wheels), "needsimg"),
    )
    assert done.returncode == 0, done.stderr
    assert not (_site(_version_dir(prepared)) / "imgdep.py").exists()


def test_a_newer_version_shadows_the_image_without_touching_it(
    prepared: Path, image: Path, tmp_path: Path
) -> None:
    """The reason this is not ``PIP_PREFIX``: pip would uninstall the image's copy first."""
    wheels = tmp_path / "wheels"
    _wheel(wheels, "imgdep", "2.0")
    before = _tree(image)
    done = _run(
        prepared / "current" / "bin" / "python",
        *("-m", "pip", "install", "--no-index", "--find-links", str(wheels), "imgdep==2.0"),
    )
    try:
        assert done.returncode == 0, done.stderr
        assert _tree(image) == before
        shown = _run(
            prepared / "current" / "bin" / "python", "-c", "import imgdep; print(imgdep.VERSION)"
        )
        assert shown.stdout.strip() == "2.0"
        assert (
            _run(
                image / "bin" / "python", "-c", "import imgdep; print(imgdep.VERSION)"
            ).stdout.strip()
            == "1.0"
        )
    finally:
        _run(prepared / "current" / "bin" / "python", "-m", "pip", "uninstall", "-y", "imgdep")


def test_a_node_that_needs_the_images_torch_installs_without_a_second_one(
    prepared: Path, tmp_path: Path
) -> None:
    """What ``accelerate`` does, with the image's torch pinned at its local ROCm version."""
    wheels = tmp_path / "wheels"
    _wheel(wheels, "needstorch", "1.0", "torch>=2.0")
    done = _run(
        prepared / "current" / "bin" / "python",
        *("-m", "pip", "install", "--no-index", "--find-links", str(wheels), "needstorch"),
    )
    assert done.returncode == 0, done.stderr
    assert (_site(_version_dir(prepared)) / "needstorch.py").is_file()
    assert not (_site(_version_dir(prepared)) / "torch.py").exists()


def test_the_images_gpu_stack_cannot_be_replaced(prepared: Path, tmp_path: Path) -> None:
    """A second torch from PyPI would persist across restarts and end GPU renders. The
    install is refused instead, so the node fails on its own and the renders keep working."""
    wheels = tmp_path / "wheels"
    _wheel(wheels, "torch", "2.10.0")
    done = _run(
        prepared / "current" / "bin" / "python",
        *("-m", "pip", "install", "--no-index", "--find-links", str(wheels), "torch==2.10.0"),
    )
    assert done.returncode != 0
    assert not (_site(_version_dir(prepared)) / "torch.py").exists()


def test_preparing_again_keeps_what_nodes_installed(prepared: Path, image: Path) -> None:
    keep = _site(_version_dir(prepared)) / "kept_by_a_node.py"
    keep.write_text("")
    done = _run(image / "bin" / "python", str(prepared / _SCRIPT))
    assert done.returncode == 0, done.stderr
    assert keep.is_file()


@pytest.fixture
def own_env(image: Path, tmp_path: Path) -> Path:
    """An environment of its own, for a test that damages it."""
    root = tmp_path / "python"
    root.mkdir()
    (root / _SCRIPT).write_bytes(_script_bytes())
    done = _run(image / "bin" / "python", str(root / _SCRIPT))
    assert done.returncode == 0, done.stderr
    return root


def _prepare_on_a_full_disk(image: Path, root: Path) -> subprocess.CompletedProcess[str]:
    """``ulimit -f 0``: every write fails with EFBIG, as ENOSPC would."""
    return subprocess.run(
        ["bash", "-c", f"ulimit -f 0; exec {image / 'bin' / 'python'} {root / _SCRIPT}"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_full_disk_does_not_disturb_a_prepared_environment(own_env: Path, image: Path) -> None:
    """The start after the disk fills, with nothing to change: no write, nothing to fail."""
    done = _prepare_on_a_full_disk(image, own_env)
    assert done.returncode == 0
    assert done.stderr == ""
    assert (own_env / "current").is_symlink()
    shown = _run(own_env / "current" / "bin" / "python", "-c", "import imgdep")
    assert shown.returncode == 0, shown.stderr


def test_a_failed_write_keeps_the_file_and_hands_back_to_the_images_python(
    own_env: Path, image: Path
) -> None:
    """A truncated .pth under a live ``current`` means no torch and a crash loop."""
    pth = _site(_version_dir(own_env)) / "hal0_image_packages.pth"
    pth.write_text("# an older image's\n")
    done = _prepare_on_a_full_disk(image, own_env)
    assert done.returncode == 0
    assert "comfyui.python_env_failed" in done.stderr
    assert pth.read_text() == "# an older image's\n"
    assert not (own_env / "current").is_symlink()
    assert [p.name for p in pth.parent.glob(".*")] == []


def test_a_truncated_pth_from_an_earlier_start_is_repaired(own_env: Path, image: Path) -> None:
    pth = _site(_version_dir(own_env)) / "hal0_image_packages.pth"
    pth.write_text("")
    done = _run(image / "bin" / "python", str(own_env / _SCRIPT))
    assert done.returncode == 0, done.stderr
    assert _run(own_env / "current" / "bin" / "python", "-c", "import imgdep").returncode == 0


def test_an_environment_that_cannot_reach_the_image_is_not_activated(
    own_env: Path, image: Path
) -> None:
    """Whatever the cause, ComfyUI without the image's packages has no torch."""
    python = _version_dir(own_env) / "bin" / "python"
    python.unlink()
    python.write_text("#!/bin/sh\nexit 1\n")
    python.chmod(0o755)
    done = _run(image / "bin" / "python", str(own_env / _SCRIPT))
    assert done.returncode == 0
    assert "comfyui.python_env_failed" in done.stderr
    assert not (own_env / "current").is_symlink()


def test_a_failure_is_reported_and_never_stops_the_start(image: Path, tmp_path: Path) -> None:
    """The start command carries on with the image's Python, and doctor reads the line."""
    root = tmp_path / "python"
    root.mkdir()
    (root / _SCRIPT).write_bytes(_script_bytes())
    # A plain file where the environment belongs: what any unwritable tree amounts to.
    (root / f"{sys.version_info.major}.{sys.version_info.minor}").write_text("in the way")
    done = _run(image / "bin" / "python", str(root / _SCRIPT))
    assert done.returncode == 0
    assert "comfyui.python_env_failed" in done.stderr
    assert not (root / "current").exists()


def test_the_start_command_falls_back_to_the_images_python(tmp_path: Path) -> None:
    """Exactly the provider's shell: no script, no environment, and ``python`` still runs."""
    bash = shutil.which("bash")
    assert bash is not None
    from hal0.providers.comfyui import python_env_preamble

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "python"
    fake.write_text('#!/bin/sh\necho "ran python $*"\n')
    fake.chmod(0o755)
    mount = tmp_path / "absent"
    payload = f"{python_env_preamble(str(mount))}exec python main.py --port 8188"
    done = subprocess.run(
        [bash, "-c", payload],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().endswith("ran python main.py --port 8188")


def test_the_environment_links_the_images_interpreter_and_does_not_copy_it(
    prepared: Path,
) -> None:
    """The image's interpreter is a small stub that needs libpython beside it: a copy would
    survive the image moving on, stay first on ``PATH``, and die in the loader."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert (_version_dir(prepared) / "bin" / f"python{version}").is_symlink()


def test_an_environment_whose_interpreter_is_gone_falls_through_to_the_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An image without the old interpreter, on a mount the script could not repoint."""
    bash = shutil.which("bash")
    assert bash is not None
    from hal0.providers.comfyui import python_env_preamble

    # A real venv whose base interpreter can then be taken away.
    base = tmp_path / "old-image"
    base.mkdir()
    base_python = base / f"python{sys.version_info.major}.{sys.version_info.minor}"
    base_python.symlink_to(Path(sys._base_executable).resolve())
    monkeypatch.setattr(sys, "_base_executable", str(base_python))
    mount = tmp_path / "mount"
    env = mount / f"{sys.version_info.major}.{sys.version_info.minor}"
    venv.EnvBuilder(symlinks=True).create(env)
    (mount / "current").symlink_to(env.name)
    shutil.rmtree(base)
    assert not (env / "bin" / "python").exists()

    image_bin = tmp_path / "image" / "bin"
    image_bin.mkdir(parents=True)
    fake = image_bin / "python"
    fake.write_text('#!/bin/sh\necho "image python $*"\n')
    fake.chmod(0o755)
    payload = f"{python_env_preamble(str(mount))}exec python main.py"
    done = subprocess.run(
        [bash, "-c", payload],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{image_bin}:/usr/bin:/bin"},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().endswith("image python main.py")


def test_the_start_command_uses_the_environment_once_prepared(prepared: Path, image: Path) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    from hal0.providers.comfyui import python_env_preamble

    payload = (
        f"{python_env_preamble(str(prepared))}"
        "exec python -c \"import sys, os; print(sys.prefix); print(os.environ['VIRTUAL_ENV'])\""
    )
    done = subprocess.run(
        [bash, "-c", payload],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{image / 'bin'}:/usr/bin:/bin"},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == [str(_version_dir(prepared))] * 2
