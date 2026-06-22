"""Prefix trie with cached top-K completions per node."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(order=True)
class _Entry:
    sort_key: tuple[int, str] = field(compare=True)
    query: str = field(compare=False)
    count: int = field(compare=False)


class TrieNode:
    """One node in the prefix trie."""

    __slots__ = ("children", "top", "is_terminal", "terminal_count", "terminal_query")

    def __init__(self) -> None:
        self.children: dict[str, TrieNode] = {}
        self.top: tuple[tuple[str, int], ...] = ()
        self.is_terminal: bool = False
        self.terminal_count: int = 0
        self.terminal_query: str = ""


class PrefixTrie:
    """
    In-memory prefix trie. Each node caches top-K (query, count) pairs
    in its subtree for O(len(prefix)) suggestion lookups.
    """

    def __init__(self, top_k: int = 10) -> None:
        self.top_k = top_k
        self.root = TrieNode()
        self._size = 0

    @property
    def size(self) -> int:
        return self._size

    def build(self, rows: list[tuple[str, int]]) -> None:
        """Rebuild trie from scratch."""
        self.root = TrieNode()
        self._size = 0
        for query, count in rows:
            node = self.root
            for ch in query:
                if ch not in node.children:
                    node.children[ch] = TrieNode()
                node = node.children[ch]
            if not node.is_terminal:
                self._size += 1
            node.is_terminal = True
            node.terminal_count = count
            node.terminal_query = query
        self._rebuild_all_tops(self.root, "")

    def _rebuild_all_tops(self, node: TrieNode, prefix: str) -> list[_Entry]:
        entries: list[_Entry] = []
        if node.is_terminal:
            q = node.terminal_query or prefix
            entries.append(_Entry((-node.terminal_count, q), q, node.terminal_count))
        for ch, child in node.children.items():
            entries.extend(self._rebuild_all_tops(child, prefix + ch))
        node.top = self._merge_top(entries)
        return entries

    def _merge_top(self, entries: list[_Entry]) -> tuple[tuple[str, int], ...]:
        if not entries:
            return ()
        entries.sort(key=lambda e: (-e.count, e.query))
        seen: set[str] = set()
        result: list[tuple[str, int]] = []
        for e in entries:
            if e.query in seen:
                continue
            seen.add(e.query)
            result.append((e.query, e.count))
            if len(result) >= self.top_k:
                break
        return tuple(result)

    def _offer(self, node: TrieNode, query: str, count: int) -> None:
        """Try to insert query into node's top-K cache."""
        current = list(node.top)
        for i, (q, c) in enumerate(current):
            if q == query:
                if c == count:
                    return
                current[i] = (query, count)
                current.sort(key=lambda x: (-x[1], x[0]))
                node.top = tuple(current[: self.top_k])
                return
        current.append((query, count))
        current.sort(key=lambda x: (-x[1], x[0]))
        node.top = tuple(current[: self.top_k])

    def _refresh_path(self, query: str, count: int) -> None:
        """Re-offer query along the path from root to terminal."""
        node = self.root
        self._offer(node, query, count)
        for ch in query:
            node = node.children[ch]
            self._offer(node, query, count)

    def add_or_update(self, query: str, count: int) -> None:
        """Insert new query or update count (counts only increase)."""
        node = self.root
        for ch in query:
            if ch not in node.children:
                node.children[ch] = TrieNode()
            node = node.children[ch]
        if not node.is_terminal:
            self._size += 1
            node.is_terminal = True
            node.terminal_query = query
        node.terminal_count = count
        self._refresh_path(query, count)

    def update_count(self, query: str, new_count: int) -> None:
        """Update an existing query's count."""
        node = self.root
        for ch in query:
            if ch not in node.children:
                self.add_or_update(query, new_count)
                return
            node = node.children[ch]
        if not node.is_terminal:
            self.add_or_update(query, new_count)
            return
        node.terminal_count = new_count
        self._refresh_path(query, new_count)

    def suggest(self, prefix: str) -> list[tuple[str, int]]:
        """Return up to top_k suggestions matching prefix."""
        if not prefix:
            return list(self.root.top)

        node = self.root
        for ch in prefix:
            if ch not in node.children:
                return []
            node = node.children[ch]

        results = [(q, c) for q, c in node.top if q.startswith(prefix)]
        if len(results) >= self.top_k:
            return results[: self.top_k]

        if len(results) < self.top_k:
            all_entries = self._collect_entries(node, prefix)
            all_entries.sort(key=lambda e: (-e.count, e.query))
            seen = {q for q, _ in results}
            for e in all_entries:
                if e.query not in seen:
                    results.append((e.query, e.count))
                    seen.add(e.query)
                if len(results) >= self.top_k:
                    break
        return results[: self.top_k]

    def _collect_entries(self, node: TrieNode, prefix: str) -> list[_Entry]:
        entries: list[_Entry] = []
        if node.is_terminal:
            q = node.terminal_query or prefix
            entries.append(_Entry((-node.terminal_count, q), q, node.terminal_count))
        for ch, child in sorted(node.children.items()):
            entries.extend(self._collect_entries(child, prefix + ch))
        return entries

    def get_count(self, query: str) -> int:
        node = self.root
        for ch in query:
            if ch not in node.children:
                return 0
            node = node.children[ch]
        return node.terminal_count if node.is_terminal else 0
