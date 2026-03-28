import pytest


def test_sanity():
    assert True


def test_negative_input():
    with pytest.raises(ValueError):
        raise ValueError("bad input")