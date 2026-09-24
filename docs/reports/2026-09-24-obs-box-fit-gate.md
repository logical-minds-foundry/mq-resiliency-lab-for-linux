# obs/logsearch merge — box-fit gate measurement (#1178, epic .github#267)

**Question (spec §5.2 / plan Task 1 gate):** does the merged obs box (metrics + log
tiers) fit the hard **18 GiB build-disk / 20 GiB guest-`virtual_size` ceiling**
(`lab/boxes/build-fatbox.sh:222-230`, #1144/#1146) — or is a trim / `virtual_size`
prerequisite needed first?

## Measurement (2026-09-24)

`qemu-img info` on the registered box images (qcow2 *actual allocated* ≈ installed
data footprint):

| Box | virtual size (partition) | disk size (installed data) |
|---|---|---|
| `obs-ubuntu2404` | 18 GiB | **5.57 GiB** |
| `logsearch-ubuntu2404` | 18 GiB | **3.29 GiB** |

Both footprints include the shared Ubuntu base. The merged box holds
`base + obs_software + logsearch_software`, so:

```
merged = base + (obs − base) + (logsearch − base) = obs + logsearch − base
       ≤ 5.57 + 3.29 = 8.86 GiB   (worst case, double-counting the base)
```

## Verdict: **FITS — gate passes, no prerequisite needed**

Even the worst-case upper bound (**8.86 GiB**, which double-counts the shared base)
sits **~9 GiB under** the 18 GiB partition and well under the 20 GiB ceiling. The
true footprint is smaller (the base is counted once). So the pushback Finding-1 risk
(a merged box overflowing the ceiling) does **not** materialize: the merge proceeds
additively into `bake-obs.yml` with no footprint trim and no `virtual_size` bump.

Proven end-to-end by the merged-box bake (this task) and the cold-rebuild VAL
(#1177).
