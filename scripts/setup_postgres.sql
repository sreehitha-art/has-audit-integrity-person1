-- UNVERIFIED. Runs once on first container start (POSTGRES_DB=anchor_research already exists).
-- Separate DBs so destructive research operations can never touch development data.
CREATE DATABASE anchor_dev OWNER anchor_user;
-- hms_research / hms_template belong to Person 1's side; create them from Person 1's schema, not here.
