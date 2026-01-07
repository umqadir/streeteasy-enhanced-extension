-- StreetSafe Database Schema
-- PostgreSQL with PostGIS extension

-- Enable PostGIS
CREATE EXTENSION IF NOT EXISTS postgis;

-- NTA Boundaries Table
-- Contains NYC Neighborhood Tabulation Area polygons
CREATE TABLE IF NOT EXISTS nta_boundaries (
  id SERIAL PRIMARY KEY,
  nta_id VARCHAR(10) UNIQUE NOT NULL,
  nta_name VARCHAR(255),
  borough VARCHAR(50),
  population INTEGER,
  geom GEOMETRY(MultiPolygon, 4326),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create spatial index
CREATE INDEX IF NOT EXISTS idx_nta_geom ON nta_boundaries USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_nta_id ON nta_boundaries(nta_id);

-- Crime Complaints Table
-- Raw complaint data from NYPD
CREATE TABLE IF NOT EXISTS crime_complaints (
  id SERIAL PRIMARY KEY,
  complaint_num VARCHAR(50) UNIQUE,
  complaint_date DATE,
  offense_desc VARCHAR(255),
  law_cat_cd VARCHAR(10),
  nta_id VARCHAR(10),
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION,
  geom GEOMETRY(Point, 4326),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_complaints_date ON crime_complaints(complaint_date);
CREATE INDEX IF NOT EXISTS idx_complaints_nta ON crime_complaints(nta_id);
CREATE INDEX IF NOT EXISTS idx_complaints_offense ON crime_complaints(offense_desc);
CREATE INDEX IF NOT EXISTS idx_complaints_geom ON crime_complaints USING GIST(geom);

-- Crime Metrics Table
-- Aggregated and computed statistics
CREATE TABLE IF NOT EXISTS crime_metrics (
  id SERIAL PRIMARY KEY,
  nta_id VARCHAR(10) NOT NULL,
  time_window VARCHAR(10) NOT NULL, -- '12m', '24m', 'ytd'
  metric_type VARCHAR(50) NOT NULL, -- 'murder', 'felonyAssault', 'violentCrime'
  count INTEGER NOT NULL,
  rate NUMERIC(10, 2), -- per 100k residents
  percentile NUMERIC(5, 2), -- 0-100
  rank INTEGER,
  total_neighborhoods INTEGER,
  computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(nta_id, time_window, metric_type, computed_at)
);

CREATE INDEX IF NOT EXISTS idx_metrics_nta ON crime_metrics(nta_id);
CREATE INDEX IF NOT EXISTS idx_metrics_window ON crime_metrics(time_window);
CREATE INDEX IF NOT EXISTS idx_metrics_computed ON crime_metrics(computed_at);

-- Crime Comparisons Table
-- NYC-wide and borough-level averages
CREATE TABLE IF NOT EXISTS crime_comparisons (
  id SERIAL PRIMARY KEY,
  time_window VARCHAR(10) NOT NULL,
  metric_type VARCHAR(50) NOT NULL,
  nyc_average NUMERIC(10, 2),
  borough_average NUMERIC(10, 2),
  borough VARCHAR(50),
  computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(time_window, metric_type, borough, computed_at)
);

CREATE INDEX IF NOT EXISTS idx_comparisons_window ON crime_comparisons(time_window);
CREATE INDEX IF NOT EXISTS idx_comparisons_computed ON crime_comparisons(computed_at);

-- Pipeline Metadata Table
-- Track pipeline runs and data freshness
CREATE TABLE IF NOT EXISTS pipeline_metadata (
  id SERIAL PRIMARY KEY,
  run_type VARCHAR(50) NOT NULL, -- 'full', 'incremental'
  status VARCHAR(20) NOT NULL, -- 'running', 'completed', 'failed'
  records_processed INTEGER,
  records_added INTEGER,
  started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMP,
  error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_pipeline_started ON pipeline_metadata(started_at);

-- Population Data Table (from ACS 5-year)
CREATE TABLE IF NOT EXISTS nta_population (
  id SERIAL PRIMARY KEY,
  nta_id VARCHAR(10) UNIQUE NOT NULL,
  population INTEGER NOT NULL,
  acs_year VARCHAR(10), -- e.g., '2019-2023'
  source VARCHAR(255),
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_population_nta ON nta_population(nta_id);
