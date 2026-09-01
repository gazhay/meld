from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class PathType(Enum):
    HARDLINK = "hardlink"
    SOFTLINK = "softlink"
    DIRECTORY = "directory"


@dataclass
class PkgPath:
    path_type: PathType
    sha256: str
    size_in_bytes: int


@dataclass
class Pkg:
    name: str
    depends: list[str]
    paths: dict[str, PkgPath]


def parse_pkg(data: dict[str, object]) -> Pkg:
    name = data["name"]
    assert isinstance(name, str)
    depends = data["depends"]
    assert isinstance(depends, list) and all(isinstance(x, str) for x in depends)
    files = data["files"]
    assert isinstance(files, list) and all(isinstance(x, str) for x in files)
    paths_data = data["paths_data"]
    assert isinstance(paths_data, dict)
    assert paths_data["paths_version"] == 1
    paths0 = paths_data["paths"]
    assert isinstance(paths0, list) and all(isinstance(x, dict) for x in paths0)
    paths: dict[str, PkgPath] = {}
    for path0 in paths0:
        _path = path0["_path"]
        assert isinstance(_path, str)
        path_type = PathType(path0["path_type"])
        sha256 = path0["sha256"]
        assert isinstance(sha256, str)
        size_in_bytes = path0["size_in_bytes"]
        assert isinstance(size_in_bytes, int)
        paths[_path] = PkgPath(path_type, sha256, size_in_bytes)
    assert set(files) == set(paths.keys())
    return Pkg(name, depends, paths)


def get_pkgs(env_root: Path) -> dict[str, Pkg]:
    r: dict[str, Pkg] = {}
    for fn in env_root.joinpath("conda-meta").glob("*.json"):
        data = json.loads(fn.read_text())
        pkg = parse_pkg(data)
        r[pkg.name] = pkg
    all_paths = [path for pkg in r.values() for path in pkg.paths.keys()]
    assert len(set(all_paths)) == len(all_paths), (
        "There is a path which belongs to multiple packages"
    )
    return r
