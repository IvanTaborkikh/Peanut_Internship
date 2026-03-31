import pytest
from core.serializer import CanonicalSerializer

def test_keys_sorted_1():
    data = {"b": 1, "a": 2}
    serialized = CanonicalSerializer.serialize(data)
    assert serialized == b'{"a":2,"b":1}'

def test_keys_sorted_2():
    data = {"a": 2, "b": 1}
    serialized = CanonicalSerializer.serialize(data)
    assert serialized == b'{"a":2,"b":1}'

def test_keys_sorted_nested():
    data = {"z": {"c": 1, "a": 2}, "a": {"z": 9, "b": 3}}
    serialized = CanonicalSerializer.serialize(data)
    assert serialized == b'{"a":{"b":3,"z":9},"z":{"a":2,"c":1}}'

def test_keys_sorted_with_list():
    data = {"items": [{"z": 1, "a": 2}, {"y": 3, "b": 4}]}
    serialized = CanonicalSerializer.serialize(data)
    assert serialized == b'{"items":[{"a":2,"z":1},{"b":4,"y":3}]}'

def test_keys_sorted_deeply_nested():
    data = {"z": {"y": {"b": 1, "a": 2}, "x": 3}, "a": 4}
    serialized = CanonicalSerializer.serialize(data)
    assert serialized == b'{"a":4,"z":{"x":3,"y":{"a":2,"b":1}}}'

def test_float_raises():
    data = {"a": 2.5, "b": 1}
    with pytest.raises(ValueError):
        CanonicalSerializer.serialize(data)

def test_unicode_strings():
    data = {"msg": "Hello 🦀"}
    serialized = CanonicalSerializer.serialize(data)
    assert serialized == '{"msg":"Hello 🦀"}'.encode('utf-8')

def test_large_integers():
    data = {"a": 9007199254740992, "b": 36028797018963968}
    serialized = CanonicalSerializer.serialize(data)
    assert serialized == b'{"a":9007199254740992,"b":36028797018963968}'

def test_none_values():
    data = {"a": None, "b": None}
    serialized = CanonicalSerializer.serialize(data)
    assert serialized == b'{"a":null,"b":null}'

def test_none_value():
    data = None
    serialized = CanonicalSerializer.serialize(data)
    assert serialized == b'null'

def test_empty_object():
    assert CanonicalSerializer.serialize({}) == b'{}'

def test_empty_array():
    assert CanonicalSerializer.serialize([]) == b'[]'

def test_float_raises_nested():
    with pytest.raises(ValueError):
        CanonicalSerializer.serialize({"a": {"b": 1.5}})

def test_verify_determinism():
    data = {"b": 2, "a": 1}
    assert CanonicalSerializer.verify_determinism(data)

def test_hash_consistency():
    data = {"b": 2, "a": 1}
    assert CanonicalSerializer.hash(data) == CanonicalSerializer.hash(data)