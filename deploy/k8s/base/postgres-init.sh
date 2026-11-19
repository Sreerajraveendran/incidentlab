#!/bin/sh
# Runs once, on the first start with an empty data volume.
# Two databases with separate owners: the hosted app's data and the platform's
# metadata never mix, and the app role cannot read platform data.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE app_user LOGIN PASSWORD '${APP_DB_PASSWORD}';
CREATE DATABASE app_db OWNER app_user;
REVOKE ALL ON DATABASE app_db FROM PUBLIC;

CREATE ROLE platform_user LOGIN PASSWORD '${PLATFORM_DB_PASSWORD}';
CREATE DATABASE platform_db OWNER platform_user;
REVOKE ALL ON DATABASE platform_db FROM PUBLIC;
SQL
