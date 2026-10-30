-- RAW layer: one all-VARCHAR table per CSV, plus load metadata.
-- Existing tables are only replaced when P_REPLACE = TRUE (explicit user action).
USE ROLE MV_APP_OWNER;
USE WAREHOUSE MEDIVERSE_WH;
USE SCHEMA MEDIVERSE360.RAW;

-- Separate format for COPY: the two metadata columns are not in the file.
CREATE FILE FORMAT IF NOT EXISTS RAW.CSV_LOAD_FMT TYPE = CSV PARSE_HEADER = TRUE
  FIELD_OPTIONALLY_ENCLOSED_BY = '"' EMPTY_FIELD_AS_NULL = TRUE ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE;

CREATE TABLE IF NOT EXISTS RAW.LOAD_LOG (
  load_id      STRING DEFAULT UUID_STRING(),
  table_name   STRING,
  file_name    STRING,
  rows_loaded  NUMBER,
  status       STRING,
  message      STRING,
  loaded_by    STRING DEFAULT CURRENT_USER(),
  loaded_at    TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE OR REPLACE PROCEDURE RAW.LOAD_RAW_TABLE(P_TABLE STRING, P_REPLACE BOOLEAN)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS OWNER
AS
$$
DECLARE
  tbl STRING;
  loc STRING;
  exists_cnt NUMBER;
  loaded NUMBER DEFAULT 0;
BEGIN
  IF (NOT REGEXP_LIKE(:P_TABLE, '^[a-z_]+$')) THEN
    RETURN 'REJECTED: invalid table name';
  END IF;
  tbl := 'RAW.' || UPPER(:P_TABLE);
  loc := '@RAW.CSV_STAGE/' || :P_TABLE || '.csv.gz';

  SELECT COUNT(*) INTO :exists_cnt FROM MEDIVERSE360.INFORMATION_SCHEMA.TABLES
   WHERE TABLE_SCHEMA = 'RAW' AND TABLE_NAME = UPPER(:P_TABLE);
  IF (exists_cnt > 0 AND NOT P_REPLACE) THEN
    INSERT INTO RAW.LOAD_LOG (table_name, file_name, rows_loaded, status, message)
      VALUES (:tbl, :loc, 0, 'SKIPPED', 'table exists; pass P_REPLACE=TRUE to reload');
    RETURN 'SKIPPED ' || tbl || ' (exists)';
  END IF;

  EXECUTE IMMEDIATE
    'CREATE OR REPLACE TABLE ' || tbl || ' USING TEMPLATE (
       SELECT ARRAY_AGG(OBJECT_CONSTRUCT(''COLUMN_NAME'', UPPER(COLUMN_NAME), ''TYPE'', ''VARCHAR'', ''NULLABLE'', TRUE))
              WITHIN GROUP (ORDER BY ORDER_ID)
       FROM TABLE(INFER_SCHEMA(LOCATION => ''' || loc || ''', FILE_FORMAT => ''RAW.CSV_FMT'')))';
  EXECUTE IMMEDIATE 'ALTER TABLE ' || tbl || ' ADD COLUMN _FILE VARCHAR, _LOADED_AT TIMESTAMP_LTZ';
  EXECUTE IMMEDIATE
    'COPY INTO ' || tbl || ' FROM ' || loc || '
       FILE_FORMAT = (FORMAT_NAME = ''RAW.CSV_LOAD_FMT'')
       MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
       INCLUDE_METADATA = (_FILE = METADATA$FILENAME, _LOADED_AT = METADATA$START_SCAN_TIME)
       ON_ERROR = ABORT_STATEMENT FORCE = TRUE';
  EXECUTE IMMEDIATE 'SELECT COUNT(*) FROM ' || tbl;
  SELECT $1 INTO :loaded FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()));
  INSERT INTO RAW.LOAD_LOG (table_name, file_name, rows_loaded, status, message)
    VALUES (:tbl, :loc, :loaded, 'LOADED', NULL);
  RETURN 'LOADED ' || tbl || ' rows=' || loaded;
EXCEPTION
  WHEN OTHER THEN
    INSERT INTO RAW.LOAD_LOG (table_name, file_name, rows_loaded, status, message)
      VALUES (:tbl, :loc, 0, 'FAILED', :SQLERRM);
    RETURN 'FAILED ' || tbl || ': ' || SQLERRM;
END;
$$;

-- Load every staged CSV.
CREATE OR REPLACE PROCEDURE RAW.LOAD_ALL_RAW(P_REPLACE BOOLEAN)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS OWNER
AS
$$
DECLARE
  out STRING DEFAULT '';
  r STRING;
  tname STRING;
  c CURSOR FOR SELECT REGEXP_SUBSTR(RELATIVE_PATH, '^([a-z_]+)[.]csv[.]gz$', 1, 1, 'e') AS t
                 FROM DIRECTORY(@RAW.CSV_STAGE) WHERE RELATIVE_PATH LIKE '%.csv.gz' ORDER BY 1;
BEGIN
  ALTER STAGE RAW.CSV_STAGE REFRESH;
  FOR rec IN c DO
    tname := rec.t;
    CALL RAW.LOAD_RAW_TABLE(:tname, :P_REPLACE) INTO :r;
    out := out || r || '\n';
  END FOR;
  RETURN out;
END;
$$;
