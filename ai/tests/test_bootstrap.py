import sys


def test_python_version():
    assert sys.version_info[:2] == (3, 12)


def test_packages_import():
    import data  # noqa: F401
    import sim  # noqa: F401
