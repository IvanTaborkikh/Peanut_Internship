from typing import Any
import json
from eth_hash.auto import keccak


class CanonicalSerializer:
    """
    Produces deterministic JSON for signing.

    Rules:
    - Keys sorted alphabetically (recursive)
    - No whitespace
    - Numbers as-is (but prefer string amounts in trading data)
    - Consistent unicode handling
    """

    @staticmethod
    def _check_no_floats(obj: Any) -> None:
        if isinstance(obj, float):
            raise ValueError("Floating point numbers are not allowed in canonical serialization.")
        elif isinstance(obj, dict):
            for value in obj.values():
                CanonicalSerializer._check_no_floats(value)
        elif isinstance(obj, list):
            for item in obj:
                CanonicalSerializer._check_no_floats(item)

    @staticmethod
    def serialize(obj: Any) -> bytes:
        CanonicalSerializer._check_no_floats(obj)
        return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')

    @staticmethod
    def hash(obj: Any) -> bytes:
        """Returns keccak256 of canonical serialization."""
        return keccak(CanonicalSerializer.serialize(obj))

    @staticmethod
    def verify_determinism(obj: Any, iterations: int = 100) -> bool:
        """Verifies serialization is deterministic over N iterations."""
        etalon = CanonicalSerializer.hash(obj)

        for _ in range(iterations):
            if CanonicalSerializer.hash(obj) != etalon:
                return False
        return True