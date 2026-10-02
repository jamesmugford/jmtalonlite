#!/usr/bin/env python3
"""Rebuild the patched PyWayland bundle used by Talon."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "third_party" / "manifest.json"
PROTOCOL_DIR = ROOT / "third_party" / "protocols"
VENDOR_ROOT = ROOT / ".vendor" / "pywayland"
CORE_PROTOCOL = "wayland.xml"

WHEEL_URL = (
    "https://files.pythonhosted.org/packages/a3/22/"
    "dc49766cb01d174d5f1f55620385436bd8032547d6196aef22c8fec991e2/"
    "pywayland-0.4.19-cp313-cp313-manylinux_2_34_x86_64.whl"
)
SOURCE_URL = (
    "https://github.com/flacjacket/pywayland/archive/"
    "7f48c575076b3e620a6ba3565dc877d3a9e665ff.tar.gz"
)
PROTOCOL_FILES = (
    "wlr-virtual-pointer-unstable-v1.xml",
    "virtual-keyboard-unstable-v1.xml",
    "wlr-foreign-toplevel-management-unstable-v1.xml",
)
GENERATED_MODULES = {
    "wayland.py",
    *(
        filename.removesuffix(".xml").replace("-", "_") + ".py"
        for filename in PROTOCOL_FILES
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path, expected_hash: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "jm-talon-lite"})
    with (
        urllib.request.urlopen(request, timeout=60) as response,
        destination.open("wb") as output,
    ):
        shutil.copyfileobj(response, output)
    actual_hash = _sha256(destination)
    if actual_hash != expected_hash:
        raise RuntimeError(
            f"Hash mismatch for {destination.name}: {actual_hash} != {expected_hash}"
        )


def _one(paths: list[Path], description: str) -> Path:
    if len(paths) != 1:
        raise RuntimeError(f"Expected one {description}, found {paths}")
    return paths[0]


def _patch_ffi(source_root: Path, wheel_site: Path) -> None:
    ffi_build = source_root / "pywayland" / "ffi_build.py"
    text = ffi_build.read_text()
    prepare_read = "int wl_display_prepare_read(struct wl_display *display);\n"
    if prepare_read not in text:
        raise RuntimeError("Could not find wl_display_prepare_read declaration")
    text = text.replace(
        prepare_read,
        prepare_read + "void wl_display_cancel_read(struct wl_display *display);\n",
        1,
    )

    original_link = (
        '    "pywayland._ffi", SOURCE, '
        'libraries=["wayland-client", "wayland-server"]\n'
    )
    libraries = wheel_site / "pywayland.libs"
    client = _one(list(libraries.glob("libwayland-client-*.so.*")), "client library")
    server = _one(list(libraries.glob("libwayland-server-*.so.*")), "server library")
    patched_link = (
        '    "pywayland._ffi",\n'
        "    SOURCE,\n"
        f"    extra_objects=[{str(client)!r}, {str(server)!r}],\n"
        '    runtime_library_dirs=["$ORIGIN/../pywayland.libs"],\n'
    )
    if original_link not in text:
        raise RuntimeError("Could not find the PyWayland linker configuration")
    ffi_build.write_text(text.replace(original_link, patched_link, 1))


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _validate_python(talon_python: Path, manifest: dict) -> tuple[Path, Path]:
    probe = subprocess.run(
        [
            str(talon_python),
            "-c",
            "import cffi, json, platform, sys, sysconfig; "
            "print(json.dumps([sysconfig.get_config_var('SOABI'), platform.machine(), "
            "platform.system(), platform.libc_ver(), cffi.__version__, "
            "sysconfig.get_path('include'), sys.prefix]))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    soabi, machine, system, libc, cffi_version, include, prefix = json.loads(
        probe.stdout
    )
    target = manifest["bundle"]["targets"].get(soabi)
    if (
        target is None
        or machine != "x86_64"
        or system != "Linux"
        or libc[0] != "glibc"
        or tuple(map(int, libc[1].split("."))) < (2, 34)
        or cffi_version != target["cffi"]
    ):
        raise RuntimeError(
            "The vendor target requires a manifest-listed Python/CFFI ABI, Linux "
            "x86-64, and glibc 2.34 or newer; "
            f"target reported {probe.stdout.strip()}"
        )
    include_path = Path(include)
    if not (include_path / "Python.h").is_file():
        include_path = Path(prefix) / "include"
    if not (include_path / "Python.h").is_file():
        raise RuntimeError("Talon Python headers are not installed")
    return include_path, VENDOR_ROOT / target["directory"]


def _trim_runtime_bundle(site: Path) -> None:
    for dist_info in site.glob("*.dist-info"):
        shutil.rmtree(dist_info)

    pywayland = site / "pywayland"
    (pywayland / "ffi_build.py").unlink()
    protocol_dir = pywayland / "protocol"
    for module in protocol_dir.glob("*.py"):
        if module.name not in GENERATED_MODULES:
            module.unlink()
    shutil.copy2(MANIFEST, site / "VENDOR.json")


def _patch_ffi_stub(site: Path) -> None:
    stub = site / "pywayland" / "_ffi" / "lib.pyi"
    text = stub.read_text()
    prepare_read = "def wl_display_prepare_read(display: WlDisplay) -> int: ...\n"
    if prepare_read not in text:
        raise RuntimeError("Could not find wl_display_prepare_read type stub")
    stub.write_text(
        text.replace(
            prepare_read,
            prepare_read
            + "def wl_display_cancel_read(display: WlDisplay) -> None: ...\n",
            1,
        )
    )


def _readelf(*args: str, path: Path) -> str:
    result = subprocess.run(
        ["readelf", *args, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _validate_elf(site: Path, extension: Path, manifest: dict) -> None:
    bundle = manifest["bundle"]
    dynamic = _readelf("-d", path=extension)
    expected_needed = {*bundle["extension_needed"], "libc.so.6"}
    actual_needed = set(re.findall(r"Shared library: \[([^]]+)]", dynamic))
    if actual_needed != expected_needed:
        raise RuntimeError(f"Patched extension dependencies are {actual_needed}")
    expected_runpath = f"Library runpath: [{bundle['extension_runpath']}]"
    if expected_runpath not in dynamic:
        raise RuntimeError("Patched extension has an unexpected RUNPATH")

    private_libraries = site / "pywayland.libs"
    actual_private = {path.name for path in private_libraries.iterdir()}
    if actual_private != set(bundle["private_libraries"]):
        raise RuntimeError(f"Unexpected private libraries: {actual_private}")
    for library_name in bundle["private_libraries"]:
        library = private_libraries / library_name
        if not library.is_file():
            raise RuntimeError(f"Missing private library: {library_name}")
        if library_name.startswith("libwayland-"):
            library_dynamic = _readelf("-d", path=library)
            ffi_name = bundle["private_libraries"][0]
            library_needed = set(
                re.findall(r"Shared library: \[([^]]+)]", library_dynamic)
            )
            if library_needed != {ffi_name, "libc.so.6"}:
                raise RuntimeError(
                    f"{library_name} dependencies are {library_needed}"
                )
            if "Library rpath: [$ORIGIN]" not in library_dynamic:
                raise RuntimeError(f"{library_name} has an unexpected RPATH")

    maximum_glibc = tuple(map(int, bundle["maximum_glibc"].split(".")))
    for binary in (extension, *private_libraries.iterdir()):
        versions = {
            tuple(map(int, match))
            for match in re.findall(
                r"GLIBC_(\d+)\.(\d+)",
                _readelf("--version-info", path=binary),
            )
        }
        if versions and max(versions) > maximum_glibc:
            raise RuntimeError(
                f"{binary.name} requires glibc {max(versions)}, above "
                f"the declared {maximum_glibc} ceiling"
            )


def build(talon_python: Path) -> None:
    manifest = json.loads(MANIFEST.read_text())
    include_path, destination = _validate_python(talon_python, manifest)
    for filename, expected_hash in manifest["protocols"].items():
        actual_hash = _sha256(PROTOCOL_DIR / filename)
        if actual_hash != expected_hash:
            raise RuntimeError(f"Protocol hash mismatch for {filename}")

    with tempfile.TemporaryDirectory(prefix="pywayland-vendor-") as temporary:
        temp = Path(temporary)
        wheel = temp / "pywayland.whl"
        source = temp / "pywayland.tar.gz"
        site = temp / "site"
        generated = temp / "generated"
        site.mkdir()
        generated.mkdir()

        _download(WHEEL_URL, wheel, manifest["pywayland"]["wheel_sha256"])
        _download(SOURCE_URL, source, manifest["pywayland"]["source_sha256"])
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(site)
        # The wheel supplies Python sources and private libraries; rebuild its
        # extension for the selected Talon ABI rather than retaining a second ABI.
        for old_extension in (site / "pywayland").glob("_ffi*.so"):
            old_extension.unlink()
        with tarfile.open(source) as archive:
            archive.extractall(temp, filter="data")

        source_root = _one(
            [path for path in temp.glob("pywayland-*") if path.is_dir()],
            "source directory",
        )
        _patch_ffi(source_root, site)

        env = os.environ.copy()
        env["CC"] = "gcc"
        env["LDSHARED"] = "gcc -shared"
        env["CFLAGS"] = f"-I{include_path}"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        _run(
            [str(talon_python), "pywayland/ffi_build.py"],
            cwd=source_root,
            env=env,
        )
        extension = _one(
            list((source_root / "pywayland").glob("_ffi*.so")),
            "CFFI extension",
        )
        shutil.copy2(extension, site / "pywayland" / extension.name)

        scan_env = env.copy()
        scan_env["PYTHONPATH"] = str(site)
        _run(
            [
                str(talon_python),
                "-m",
                "pywayland.scanner",
                "--output-dir",
                str(generated),
                "--input",
                str(PROTOCOL_DIR / CORE_PROTOCOL),
                *(str(PROTOCOL_DIR / filename) for filename in PROTOCOL_FILES),
            ],
            cwd=ROOT,
            env=scan_env,
        )
        for filename in (CORE_PROTOCOL, *PROTOCOL_FILES):
            module_name = filename.removesuffix(".xml").replace("-", "_") + ".py"
            generated_module = generated / module_name
            expected_hash = manifest["generated_protocols"][module_name]
            if _sha256(generated_module) != expected_hash:
                raise RuntimeError(f"Generated protocol hash mismatch for {module_name}")
            shutil.copy2(generated_module, site / "pywayland" / "protocol")
        _patch_ffi_stub(site)
        _trim_runtime_bundle(site)
        _validate_elf(site, site / "pywayland" / extension.name, manifest)

        verify = (
            "from pywayland import lib; "
            "from pywayland.protocol.virtual_keyboard_unstable_v1 import "
            "ZwpVirtualKeyboardManagerV1; "
            "from pywayland.protocol.wlr_foreign_toplevel_management_unstable_v1 "
            "import ZwlrForeignToplevelManagerV1; "
            "from pywayland.protocol.wlr_virtual_pointer_unstable_v1 import "
            "ZwlrVirtualPointerManagerV1; "
            "assert hasattr(lib, 'wl_display_cancel_read')"
        )
        _run([str(talon_python), "-c", verify], cwd=ROOT, env=scan_env)

        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(site, destination)

    print(f"Rebuilt {destination}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python",
        type=Path,
        default=Path.home() / ".talon" / "bin" / "python",
        help="Talon CPython executable",
    )
    args = parser.parse_args()
    if not args.python.is_file():
        parser.error(f"Talon Python not found: {args.python}")
    build(args.python.expanduser())


if __name__ == "__main__":
    main()
