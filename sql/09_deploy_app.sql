-- Deploy the Streamlit app with plain SQL (no Snowflake CLI required).
-- Run from a client that supports PUT (SnowSQL, Snowflake CLI `snow sql -f`, or an IDE
-- connection) with the working directory set to the project root. Adjust the local path below.
USE ROLE MV_APP_OWNER;
USE WAREHOUSE MEDIVERSE_WH;
CREATE STAGE IF NOT EXISTS MEDIVERSE360.APP.APP_STAGE DIRECTORY = (ENABLE = TRUE) ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');

PUT 'file://c:/hospitalrecord/streamlit_app.py'        @MEDIVERSE360.APP.APP_STAGE/app/                    AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://c:/hospitalrecord/pyproject.toml'          @MEDIVERSE360.APP.APP_STAGE/app/                    AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://c:/hospitalrecord/.streamlit/config.toml'  @MEDIVERSE360.APP.APP_STAGE/app/.streamlit/         AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://c:/hospitalrecord/app_pages/*.py'          @MEDIVERSE360.APP.APP_STAGE/app/app_pages/          AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://c:/hospitalrecord/mediverse/*.py'          @MEDIVERSE360.APP.APP_STAGE/app/mediverse/          AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://c:/hospitalrecord/mediverse/auth/*.py'     @MEDIVERSE360.APP.APP_STAGE/app/mediverse/auth/     AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://c:/hospitalrecord/mediverse/biometrics/*.py' @MEDIVERSE360.APP.APP_STAGE/app/mediverse/biometrics/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;

CREATE STREAMLIT IF NOT EXISTS MEDIVERSE360.APP.MEDIVERSE360_APP
  FROM '@MEDIVERSE360.APP.APP_STAGE/app'
  MAIN_FILE = 'streamlit_app.py'
  RUNTIME_NAME = 'SYSTEM$ST_CONTAINER_RUNTIME_PY3_11'
  COMPUTE_POOL = SYSTEM_COMPUTE_POOL_CPU
  QUERY_WAREHOUSE = MEDIVERSE_WH
  ARTIFACT_REPOSITORIES = (snowflake.snowpark.pypi_shared_repository)
  TITLE = 'MediVerse 360'
  COMMENT = 'MediVerse 360 - synthetic demo data only';
-- After changing files on the stage, refresh the live version:
-- ALTER STREAMLIT MEDIVERSE360.APP.MEDIVERSE360_APP ADD LIVE VERSION FROM LAST;
