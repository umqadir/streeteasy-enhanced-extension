# StreetSafe Methodology

Detailed explanation of how StreetSafe computes and presents crime statistics.

## Data Sources

### NYPD Complaint Data

**Source**: [NYC Open Data - NYPD Complaint Data Current (Year To Date)](https://data.cityofnewyork.us/Public-Safety/NYPD-Complaint-Data-Current-Year-To-Date-/5uac-w243)

**Update Frequency**: Quarterly (with several months lag)

**What It Is**:
- Complaint-level incident reports from NYPD
- Includes date, location, offense type, and classification
- Each row represents a single complaint filed with NYPD

**What It's Not**:
- Not real-time (several months lag)
- Not all crimes (only reported complaints)
- Not all geocoded (sensitive crimes like sexual assaults may lack coordinates)

**Fields Used**:
- `cmplnt_num`: Unique complaint ID
- `cmplnt_fr_dt`: Complaint date
- `ofns_desc`: Offense description (e.g., "FELONY ASSAULT")
- `law_cat_cd`: Law category code (e.g., "FELONY", "MISDEMEANOR")
- `latitude` / `longitude`: Location coordinates

### Geography: Neighborhood Tabulation Areas (NTAs)

**Source**: [NYC Planning - Neighborhood Tabulation Areas](https://data.cityofnewyork.us/City-Government/Neighborhood-Tabulation-Areas-NTA-/cpf4-rkhq)

**What They Are**:
- Statistical geographies created by NYC Planning
- Designed for Census/ACS data aggregation
- Approximately 195 areas covering all 5 boroughs
- Boundaries attempt to approximate neighborhood boundaries but prioritize statistical consistency

**What They're Not**:
- Not official neighborhood boundaries
- Not perfect matches for subjective "neighborhood" definitions
- May not align with community district or police precinct boundaries

**Why We Use NTAs**:
1. **Standardized**: Stable, consistent geographies for year-over-year comparison
2. **Census-aligned**: Match population data from ACS
3. **Citywide**: Complete coverage of NYC
4. **Documented**: Well-defined and publicly available

### Population Data

**Source**: [NYC Planning - ACS 5-Year Demographic Data](https://www.nyc.gov/site/planning/data-maps/open-data/census-download-metadata.page)

**What It Is**:
- American Community Survey (ACS) 5-year estimates
- Pooled survey data from 2019-2023
- Total population per NTA

**Why 5-Year Estimates**:
- Most reliable for small geographies (NTAs)
- Recommended by Census Bureau for areas under 65,000 people
- Larger sample size than 1-year estimates

**Limitations**:
- Mid-period estimate (effectively ~2021)
- May not reflect recent population changes
- Survey-based, has margin of error

## Crime Categories

### Murder

**NYPD Definition**: `MURDER & NON-NEGL. MANSLAUGHTER`

**Includes**:
- Intentional killing of another person
- Non-negligent manslaughter

**Law Category**: FELONY

**Reporting**: Generally high reporting rate (most serious crime)

### Felony Assault

**NYPD Definition**: `FELONY ASSAULT`

**Includes**:
- Assault causing serious physical injury
- Assault with a weapon
- Assault on protected persons (police, etc.)

**Law Category**: FELONY

**Reporting**: Moderate to high reporting rate

### Not Included (Future Consideration)

- **Robbery**: Taking property by force
- **Burglary**: Unlawful entry into a building
- **Rape/Sexual Assault**: Often not geocoded in public data
- **Misdemeanor Assault**: Less serious assaults

## Metrics Computation

### Count

**Definition**: Total number of complaints in the NTA during the time window

**Formula**:
```
Count = Σ(complaints in NTA for time window)
```

**Purpose**:
- Shows absolute incident volume
- Important for understanding actual risk
- Reveals patterns in low-population areas

**Interpretation**:
- Higher count = more incidents
- Must be considered alongside population

### Rate (per 100,000)

**Definition**: Number of incidents per 100,000 residents

**Formula**:
```
Rate = (Count / Population) × 100,000
```

**Purpose**:
- Normalizes for population size
- Enables fair comparison between large and small neighborhoods
- Standard metric in criminology

**Interpretation**:
- Higher rate = higher risk per resident
- Useful for comparing dissimilar neighborhoods
- Can be skewed in very low-population areas

**Example**:
- Neighborhood A: 10 murders, 50,000 people → Rate = 20.0
- Neighborhood B: 2 murders, 5,000 people → Rate = 40.0
- B has fewer murders but higher rate (riskier per capita)

### Percentile

**Definition**: Percentage of NYC neighborhoods with a *lower* crime rate

**Formula**:
```
Percentile = (1 - PERCENT_RANK(rate)) × 100
```

Where `PERCENT_RANK` ranks rates from lowest to highest.

**Purpose**:
- Intuitive relative safety measure
- Independent of absolute numbers

**Interpretation**:
- 90th percentile = safer than 90% of NYC neighborhoods
- 10th percentile = safer than only 10% of neighborhoods
- 50th percentile = median safety

**Color Coding**:
- 75-100%: Green (Very safe relative to NYC)
- 50-75%: Yellow (Average to above average)
- 25-50%: Orange (Below average)
- 0-25%: Red (Well below average)

### Rank

**Definition**: Position when all NTAs are sorted by rate (ascending)

**Formula**:
```
Rank = ROW_NUMBER() OVER (ORDER BY rate ASC)
```

**Purpose**:
- Absolute position in safety ranking
- Easy to understand ("1st safest", "195th safest")

**Interpretation**:
- Rank 1 = lowest crime rate (safest)
- Rank 195 = highest crime rate (least safe)
- Middle ranks (~97-98) = median

## Time Windows

### Last 12 Months (12m)

**Definition**: Rolling 12-month window ending today

**Calculation**:
```sql
WHERE complaint_date >= (CURRENT_DATE - INTERVAL '12 months')
  AND complaint_date <= CURRENT_DATE
```

**Purpose**:
- Most recent trends
- Seasonal balance (full year cycle)

### Last 24 Months (24m)

**Definition**: Rolling 24-month window ending today

**Purpose**:
- Larger sample size (reduces noise)
- Smooths short-term fluctuations
- Better for low-crime areas

### Calendar Year (ytd)

**Definition**: January 1 of current year to today

**Purpose**:
- Year-over-year comparison
- Aligned with official crime reports

**Note**: Early in the year, ytd may have very small sample size.

## Comparisons

### NYC Average

**Definition**: Crime rate across all NTAs, weighted by population

**Formula**:
```
NYC Average = (Σ complaints citywide / Σ population citywide) × 100,000
```

**Purpose**: Citywide benchmark for comparison

### Borough Average

**Definition**: Crime rate within the borough, weighted by population

**Formula**:
```
Borough Average = (Σ complaints in borough / Σ population in borough) × 100,000
```

**Purpose**: More relevant local benchmark

## Why Both Rates AND Counts?

We display both metrics because each tells a different story:

### Rates (per 100k)
- **Pro**: Fair comparison across neighborhoods
- **Pro**: Adjusts for population size
- **Con**: Can be misleading in low-population areas
- **Con**: A single incident in a small area = high rate

### Counts (absolute numbers)
- **Pro**: Shows actual incident volume
- **Pro**: Reveals true risk exposure
- **Con**: Hard to compare across neighborhoods
- **Con**: Favors small neighborhoods unfairly

### Example: Business District

Consider a business district with:
- Population: 2,000 (mostly daytime workers, few residents)
- Murders: 4
- Rate: 200 per 100k (very high!)
- Rank: 190/195 (near bottom)

**Rate perspective**: Extremely dangerous (200 per 100k vs NYC average ~5)

**Count perspective**: 4 murders is moderate absolute risk

**Reality**: High rate reflects mismatch between daytime activity and residential population. The *count* gives better sense of actual risk.

**Our approach**: Show both, let user decide which is more relevant to their situation.

## Data Quality and Limitations

### Underreporting

Not all crimes are reported to police. Reporting rates vary by:
- Crime type (murder ~100%, assault ~50-60%, rape ~20-30%)
- Neighborhood trust in police
- Victim demographics
- Severity of incident

### Geocoding Gaps

Some complaints lack coordinates:
- Sensitive crimes (sexual assaults) often not mapped
- Errors in address data
- Incidents with only intersection or area description

**Impact**: Rates may underestimate certain crime types.

### Population Mismatches

ACS population is *residential* population, not total people present:
- Business districts: High daytime population not counted
- Tourist areas: Visitors not in population
- Universities: Students may be counted elsewhere
- Rapidly changing neighborhoods: Population data lags

**Impact**: Rates can be artificially high or low.

### Boundary Artifacts

NTAs have arbitrary boundaries:
- Crime near boundary could easily be in adjacent NTA
- Small geographic shifts = large rank changes
- Boundaries don't match mental maps

**Impact**: Rankings may not reflect perceived safety.

### Temporal Lag

NYPD data is updated quarterly with months of lag:
- Data may be 3-6 months old
- Recent trends not captured
- Incident dates may be corrected later

**Impact**: Not reflecting current conditions.

## Best Practices for Interpretation

### Do:
✅ Use as ONE factor in neighborhood evaluation
✅ Compare rates AND counts
✅ Consider context (business district vs residential)
✅ Look at multiple time windows
✅ Visit the neighborhood in person
✅ Talk to current residents
✅ Consider your personal risk factors

### Don't:
❌ Use as sole decision factor
❌ Assume past = future
❌ Ignore counts when rate is high
❌ Compare across different time windows
❌ Trust rankings within a few positions
❌ Apply broadly to whole neighborhood (crime is hyperlocal)

## Statistical Notes

### Sample Size Considerations

Small NTAs with few crimes have unstable rates:
- 1 murder in 1,000 people = 100 per 100k
- 2 murders = 200 per 100k (100% increase from 1 incident!)

**Solution**: Look at absolute counts and use longer time windows.

### Confidence Intervals

We don't currently display confidence intervals, but rates have margins of error:
- Large NTAs: Narrow confidence intervals
- Small NTAs: Wide confidence intervals
- Rare crimes (murder): Wide intervals

### Regression to the Mean

Extreme values one year tend toward average the next:
- NTA with spike in crime likely to decrease
- NTA with unusually low crime likely to increase

**Implication**: Rankings are somewhat noisy year-to-year.

## Comparison to Other Sources

### NYPD CompStat

NYPD's official crime statistics:
- **Similarity**: Same underlying data
- **Difference**: Different geography (precincts vs NTAs), different time windows, may include non-geocoded complaints

### NYC Crime Map

NYPD's public crime map:
- **Similarity**: Same data source
- **Difference**: Point-level (not aggregated), includes more crime types

### NeighborhoodScout, Niche, etc.

Private crime data aggregators:
- **Similarity**: Often use NYPD data
- **Difference**: Proprietary scoring algorithms, may include additional data sources

### Our Approach

StreetSafe is **transparent and open-source**:
- Clear methodology
- Auditable code
- Reproducible computations
- No proprietary "scores"

## Future Improvements

Potential enhancements we're considering:

1. **Confidence Intervals**: Show margin of error for rates
2. **Temporal Trends**: Charts showing change over time
3. **Spatial Smoothing**: Borrow strength from neighboring NTAs
4. **Bayesian Shrinkage**: Adjust extreme values in small areas
5. **Risk Adjustment**: Account for daytime vs residential population
6. **Additional Crimes**: Robbery, burglary, grand larceny
7. **Clearance Rates**: Show what % of crimes result in arrest

## References

- NYC Open Data: https://data.cityofnewyork.us/
- NYC Planning NTA Documentation: https://www.nyc.gov/site/planning/data-maps/open-data/census-download-metadata.page
- Census Bureau ACS Documentation: https://www.census.gov/programs-surveys/acs
- NYPD Crime Statistics: https://www.nyc.gov/site/nypd/stats/crime-statistics/crime-statistics-landing.page

---

**Questions or concerns about methodology?** Open an issue on GitHub or contact via the project page.
