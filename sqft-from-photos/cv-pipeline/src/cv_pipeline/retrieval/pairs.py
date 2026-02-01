from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PairSelectionResult:
    pairs: list[tuple[int, int]]
    diagnostics: dict[str, object]


def build_topk_pairs(
    embeddings: np.ndarray,
    *,
    k: int = 10,
    min_cosine_sim: float = 0.2,
    mutual: bool = True,
) -> PairSelectionResult:
    """
    Build candidate pairs using top-k cosine similarity per image.

    embeddings: (N, D) L2-normalized embeddings.
    Returns pairs as (i, j) with i < j.
    """
    if embeddings.ndim != 2:
        raise ValueError("embeddings must be (N, D)")
    n = int(embeddings.shape[0])
    if n < 2:
        return PairSelectionResult(pairs=[], diagnostics={"n": n, "k": k, "min_cosine_sim": min_cosine_sim})

    k_eff = int(min(max(1, k), n - 1))
    sims = embeddings @ embeddings.T  # cosine
    np.fill_diagonal(sims, -np.inf)

    # For each row, take top-k indices.
    nn = np.argpartition(-sims, kth=k_eff - 1, axis=1)[:, :k_eff]

    pairs_set: set[tuple[int, int]] = set()
    for i in range(n):
        for j in nn[i]:
            j = int(j)
            if sims[i, j] < float(min_cosine_sim):
                continue
            a, b = (i, j) if i < j else (j, i)
            pairs_set.add((a, b))

    if mutual:
        mutual_pairs: set[tuple[int, int]] = set()
        nn_sets = [set(map(int, row)) for row in nn]
        for a, b in pairs_set:
            if b in nn_sets[a] and a in nn_sets[b]:
                mutual_pairs.add((a, b))
        pairs_set = mutual_pairs

    pairs = sorted(pairs_set)
    return PairSelectionResult(
        pairs=pairs,
        diagnostics={
            "n": n,
            "k": k_eff,
            "mutual": bool(mutual),
            "min_cosine_sim": float(min_cosine_sim),
            "n_pairs": int(len(pairs)),
        },
    )


def connected_components_from_pairs(n: int, pairs: list[tuple[int, int]]) -> list[list[int]]:
    """
    Returns connected components over n nodes given an undirected edge list.
    """
    if n <= 0:
        return []
    adj: list[list[int]] = [[] for _ in range(n)]
    for a, b in pairs:
        if a == b:
            continue
        if 0 <= a < n and 0 <= b < n:
            adj[a].append(b)
            adj[b].append(a)

    seen = [False] * n
    comps: list[list[int]] = []
    for i in range(n):
        if seen[i]:
            continue
        stack = [i]
        seen[i] = True
        comp = []
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in adj[u]:
                if not seen[v]:
                    seen[v] = True
                    stack.append(v)
        comps.append(sorted(comp))
    comps.sort(key=len, reverse=True)
    return comps

