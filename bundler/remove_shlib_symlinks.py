from __future__ import annotations

import re
from pathlib import Path

MACH_O_MAGICS = (
    b"\xcf\xfa\xed\xfe",  # 64-bit Mach-O
    b"\xca\xfe\xba\xbe",  # Universal ("fat") binary
)

BINARY_MAGICS = (
    b"\x7fELF",
    b"GOBJ",
) + MACH_O_MAGICS


def get_shlib_symlinks(libdir: Path) -> dict[str, list[str]]:
    """
    Given a libdir, which is conda_env/lib, find all solib symlinks.
    Return a dict matching the target name to its source symlinks, eg.
    {'libEGL.so.1.1.0': ['libEGL.so', 'libEGL.so.1']}
    """
    shlib_symlinks: dict[str, list[str]] = {}
    fns = sorted(libdir.iterdir())  # sort to get a nice result for debugging
    for fn in fns:
        if (
            "so" in fn.name.split(".") or fn.name.endswith(".dylib")
        ) and fn.is_symlink():
            target = fn
            while target.is_symlink():
                rel_target = target.readlink()
                if len(rel_target.parts) != 1:
                    raise RuntimeError(
                        f"Expecting all shlib symlinks to be in the same path, found"
                        f" {rel_target}"
                    )
                target = libdir.joinpath(rel_target)
            shlib_symlinks.setdefault(target.name, []).append(fn.name)
    return shlib_symlinks


def get_min_len_name(all_names: list[str]) -> str:
    min_len = min(len(name) for name in all_names)
    min_len_names = [name for name in all_names if len(name) == min_len]
    if len(min_len_names) != 1:
        raise ValueError(f"Expecting exactly one minimum length name, got {all_names}")
    (min_len_name,) = min_len_names
    return min_len_name


def remove_shlib_symlinks(libdir: Path, shlib_symlinks: dict[str, list[str]]) -> None:
    """
    Remove the symlink shlibs, leave only regular files, renaming them to the shortest
    name in the group.
    """
    for src, targets in shlib_symlinks.items():
        all_names = [src, *targets]
        min_len_name = get_min_len_name(all_names)
        for name in targets:
            symlink = libdir.joinpath(name)
            assert symlink.is_symlink()
            symlink.unlink()
        if src != min_len_name:
            src = libdir.joinpath(src)
            assert not src.is_symlink()
            src.rename(libdir.joinpath(min_len_name))


def get_shlib_subs(shlib_symlinks: dict[str, list[str]]) -> dict[bytes, bytes]:
    """
    For each symlink group, find the shortest name - that's the name that all others
    should be replaced to.
    Return a dict from each of the long names to the shortest name.
    (The dict is in bytes, since it's more useful for the replacement functions)
    """
    subs: dict[bytes, bytes] = {}
    for src, targets in shlib_symlinks.items():
        all_names = [src, *targets]
        min_len_name = get_min_len_name(all_names)
        for name in all_names:
            if name != min_len_name:
                subs[name.encode("ascii")] = min_len_name.encode("ascii")
    return subs


def get_shlib_re(subs: dict[bytes, bytes]) -> re.Pattern[bytes]:
    # We sort by reverse length, so if one string is a prefix of another, the
    # longer one will match
    names = sorted(subs.keys(), key=len, reverse=True)
    return re.compile(b"|".join(re.escape(name) for name in names))


def sub_shlibs_in_text(
    subs: dict[bytes, bytes], shlib_re: re.Pattern[bytes], src: bytes
) -> bytes:
    """
    Substitute shlib names in a text, by simply replacing the long string with a short
    one.
    """
    return shlib_re.sub(lambda m: subs[m.group()], src)


def sub_shlibs_in_binary(
    subs: dict[bytes, bytes], shlib_re: re.Pattern[bytes], src: bytes
) -> bytes:
    """
    Substitute shlib names in a binary file, where the size must be kept, strings are
    null-terminated, and so to replace a string with a shorter one you replace the
    tail with null bytes.

    This gets complicated by strings such as "libharfbuzz-gobject.so.0,libharfbuzz.so.0"
    in a binary typelib file. In this case, the entire string is substituted by
    sub_shlibs_in_text().
    """
    result = bytearray(src)

    def put_in_result(pos: int, long_str: bytes, short_str: bytes):
        result[pos : pos + len(short_str)] = short_str
        n_zeros = len(long_str) - len(short_str)
        result[pos + len(short_str) : pos + len(long_str)] = b"\0" * n_zeros

    pos = 0
    while True:
        m = shlib_re.search(src, pos)
        if m is None:
            break
        long_name = m.group()
        short_name = subs[long_name]
        next_char = src[m.end()]
        if next_char == 0:
            put_in_result(m.start(), long_name, short_name)
            pos = m.end()
        elif next_char == ord(","):
            str_endpos = src.find(b"\0", m.end())
            if str_endpos == -1:
                raise ValueError("Expecting string to end with a null byte")
            long_str = src[m.start() : str_endpos]
            short_str = sub_shlibs_in_text(subs, shlib_re, long_str)
            put_in_result(m.start(), long_str, short_str)
            pos = str_endpos
        else:
            raise ValueError(
                f"Expecting null byte or ',' at {m.end()}, is {chr(next_char)!r}"
            )
    return bytes(result)


def sub_shlibs_in_something(
    subs: dict[bytes, bytes], shlib_re: re.Pattern[bytes], src: bytes
) -> bytes:
    m = shlib_re.search(src)
    if m is None:
        return src
    magic = src[:4]
    if magic in BINARY_MAGICS:
        return sub_shlibs_in_binary(subs, shlib_re, src)
    elif magic == b"<?xm":
        return sub_shlibs_in_text(subs, shlib_re, src)
    else:
        raise RuntimeError(
            f"File contains the name of a solib at position {m.pos} ({m.group()})"
            f" but is not of a supported format"
        )


def test_sub_shlibs():
    shlib_symlinks = {
        "libharfbuzz-gobject.so.0.61431.0": [
            "libharfbuzz-gobject.so",
            "libharfbuzz-gobject.so.0",
        ],
        "libharfbuzz.so.0.61431.0": ["libharfbuzz.so", "libharfbuzz.so.0"],
    }
    subs = get_shlib_subs(shlib_symlinks)
    expected_subs = {
        b"libharfbuzz-gobject.so.0.61431.0": b"libharfbuzz-gobject.so",
        b"libharfbuzz-gobject.so.0": b"libharfbuzz-gobject.so",
        b"libharfbuzz.so.0.61431.0": b"libharfbuzz.so",
        b"libharfbuzz.so.0": b"libharfbuzz.so",
    }
    assert subs == expected_subs
    shlib_re = get_shlib_re(subs)
    b0 = b"GOBJ\1\2\3libharfbuzz-gobject.so.0,libbla,libharfbuzz.so.0\0FOO"
    b1 = sub_shlibs_in_something(subs, shlib_re, b0)
    assert b1 == b"GOBJ\1\2\3libharfbuzz-gobject.so,libbla,libharfbuzz.so\0\0\0\0\0FOO"


# Ideally this would have been with pytest, but since it's a single fast function,
# we'll just do it every import
test_sub_shlibs()
