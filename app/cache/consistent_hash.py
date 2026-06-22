"""Consistent-hash ring for distributed cache routing."""

from __future__ import annotations

import hashlib
import bisect
from typing import Optional


def _hash(key: str) -> int:
    """64-bit hash for ring placement."""
    digest = hashlib.md5(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


class HashRing:
    """
    Consistent hashing ring with virtual nodes.
    Adding/removing a node remaps only ~1/N keys on average.
    """

    def __init__(self, vnodes_per_node: int = 150) -> None:
        self.vnodes_per_node = vnodes_per_node
        self._ring: list[int] = []
        self._hash_to_node: dict[int, str] = {}
        self._nodes: set[str] = set()

    @property
    def nodes(self) -> list[str]:
        return sorted(self._nodes)

    def add_node(self, name: str) -> list[tuple[str, str, str]]:
        """
        Add a node to the ring.
        Returns list of (key, old_node, new_node) for remapped keys.
        """
        if name in self._nodes:
            return []
        self._nodes.add(name)
        remaps: list[tuple[str, str, str]] = []
        for i in range(self.vnodes_per_node):
            vnode_key = f"{name}#{i}"
            h = _hash(vnode_key)
            old_owner = self._owner_for_hash(h) if self._ring else None
            self._ring.append(h)
            self._hash_to_node[h] = name
            if old_owner and old_owner != name:
                remaps.append((vnode_key, old_owner, name))
        self._ring.sort()
        return remaps

    def remove_node(self, name: str) -> list[tuple[str, str, str]]:
        """Remove a node and return remapped virtual-node keys."""
        if name not in self._nodes:
            return []
        remaps: list[tuple[str, str, str]] = []
        to_remove: list[int] = []
        for h, node in list(self._hash_to_node.items()):
            if node == name:
                to_remove.append(h)
        for h in to_remove:
            del self._hash_to_node[h]
            self._ring.remove(h)
        self._nodes.discard(name)
        for h in to_remove:
            new_owner = self._owner_for_hash(h)
            if new_owner:
                remaps.append((f"vnode@{h}", name, new_owner))
        return remaps

    def _owner_for_hash(self, h: int) -> Optional[str]:
        if not self._ring:
            return None
        idx = bisect.bisect(self._ring, h)
        if idx == len(self._ring):
            idx = 0
        return self._hash_to_node[self._ring[idx]]

    def get_node(self, key: str) -> Optional[str]:
        """Return the node responsible for key."""
        if not self._ring:
            return None
        h = _hash(key)
        idx = bisect.bisect(self._ring, h)
        if idx == len(self._ring):
            idx = 0
        return self._hash_to_node[self._ring[idx]]

    def keys_moved_on_add(self, sample_keys: list[str], new_node: str) -> int:
        """Count how many sample keys would move to new_node after adding it."""
        if new_node in self._nodes:
            return 0
        moved = 0
        for key in sample_keys:
            old = self.get_node(key)
            # Simulate add
            h = _hash(key)
            ring_copy = list(self._ring)
            for i in range(self.vnodes_per_node):
                vnode_h = _hash(f"{new_node}#{i}")
                ring_copy.append(vnode_h)
            ring_copy.sort()
            idx = bisect.bisect(ring_copy, h)
            if idx == len(ring_copy):
                idx = 0
            # Find owner in expanded ring — simplified: check if new node is closer
            new_owner = self._simulate_owner(h, new_node)
            if old != new_owner:
                moved += 1
        return moved

    def _simulate_owner(self, h: int, added_node: str) -> str:
        """Simulate ring with an additional node."""
        extra: list[tuple[int, str]] = []
        for n in self._nodes:
            for i in range(self.vnodes_per_node):
                extra.append((_hash(f"{n}#{i}"), n))
        for i in range(self.vnodes_per_node):
            extra.append((_hash(f"{added_node}#{i}"), added_node))
        extra.sort()
        for vnode_h, node in extra:
            if vnode_h >= h:
                return node
        return extra[0][1] if extra else added_node
