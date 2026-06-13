#!/usr/bin/env python3
"""
Compile NTA "exposure" inputs for the extension:

- 2020 Decennial population by NTA (residents)
- LODES WAC jobs by NTA (daytime workers)

Writes: selfhost/extension/data/nta-exposure.json
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_DIR = REPO_ROOT / "selfhost" / "extension"
OUT_PATH = EXTENSION_DIR / "data" / "nta-exposure.json"

BOUNDARIES_PATH = EXTENSION_DIR / "data" / "nta-boundaries.json"

DATA_DIR = (REPO_ROOT / ".." / "data").resolve()
POP_PATH = DATA_DIR / "Census-Decennial-2020" / "nyc_nta_pop_2020.csv"
TRACT_TO_NTA_PATH = DATA_DIR / "Census-Decennial-2020" / "nyc_tract_to_nta2020.csv"
LODES_WAC_BG_PARQUET = DATA_DIR / "LODES" / "lodes_wac_block_groups.parquet"


def main() -> None:
  boundaries = json.loads(BOUNDARIES_PATH.read_text())
  nta_ids = sorted(boundaries["boundaries"].keys())

  pop = pd.read_csv(POP_PATH, dtype={"ntacode": str})
  pop = pop.rename(columns={"population_2020": "population"})
  pop = pop[["ntacode", "population"]]

  tract_to_nta = pd.read_csv(TRACT_TO_NTA_PATH, dtype={"geoid": str, "ntacode": str})
  tract_to_nta = tract_to_nta[["geoid", "ntacode"]].dropna()

  lodes_tbl = pq.read_table(
    LODES_WAC_BG_PARQUET,
    columns=["tract_id", "jobs_wac", "lodes_year", "state_fips"],
  )
  lodes_df = lodes_tbl.to_pandas()

  lodes_df = lodes_df[lodes_df["state_fips"] == "36"].copy()
  if lodes_df.empty:
    raise RuntimeError("LODES parquet contains no NY (state_fips=36) rows")

  lodes_year = int(lodes_df["lodes_year"].max())
  lodes_df = lodes_df[lodes_df["lodes_year"] == lodes_year]

  jobs_by_tract = (
    lodes_df.groupby("tract_id", as_index=False)["jobs_wac"].sum().rename(columns={"tract_id": "geoid"})
  )

  jobs_joined = tract_to_nta.merge(jobs_by_tract, on="geoid", how="left")
  jobs_joined["jobs_wac"] = jobs_joined["jobs_wac"].fillna(0).astype("int64")
  jobs_by_nta = jobs_joined.groupby("ntacode", as_index=False)["jobs_wac"].sum()

  pop_by_nta = dict(zip(pop["ntacode"], pop["population"].astype("int64")))
  jobs_by_nta_map = dict(zip(jobs_by_nta["ntacode"], jobs_by_nta["jobs_wac"].astype("int64")))

  exposures: dict[str, dict[str, int]] = {}
  missing_pop = []

  for nta_id in nta_ids:
    population = int(pop_by_nta.get(nta_id, 0))
    jobs_wac = int(jobs_by_nta_map.get(nta_id, 0))
    if population <= 0:
      missing_pop.append(nta_id)
    exposures[nta_id] = {"population": population, "jobsWac": jobs_wac}

  if missing_pop:
    raise RuntimeError(f"Missing population for {len(missing_pop)} NTAs, e.g. {missing_pop[:10]}")

  payload = {
    "generatedAt": datetime.now(timezone.utc).isoformat(),
    "populationYear": 2020,
    "lodesYear": lodes_year,
    "exposures": exposures,
  }

  OUT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
  print(f"Wrote {OUT_PATH} ({len(exposures)} NTAs, LODES {lodes_year})")


if __name__ == "__main__":
  main()
