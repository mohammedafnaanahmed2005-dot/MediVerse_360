-- Push the current local code into the deployed app's LIVE version, then commit it.
-- Run from a client that supports PUT, from the project root (adjust the path).
USE ROLE MV_APP_OWNER;
PUT 'file://c:/hospitalrecord/streamlit_app.py' 'snow://streamlit/MEDIVERSE360.APP.MEDIVERSE360_APP/versions/live/' AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://c:/hospitalrecord/pyproject.toml' 'snow://streamlit/MEDIVERSE360.APP.MEDIVERSE360_APP/versions/live/' AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://c:/hospitalrecord/app_pages/*.py' 'snow://streamlit/MEDIVERSE360.APP.MEDIVERSE360_APP/versions/live/app_pages/' AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://c:/hospitalrecord/mediverse/*.py' 'snow://streamlit/MEDIVERSE360.APP.MEDIVERSE360_APP/versions/live/mediverse/' AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://c:/hospitalrecord/mediverse/auth/*.py' 'snow://streamlit/MEDIVERSE360.APP.MEDIVERSE360_APP/versions/live/mediverse/auth/' AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://c:/hospitalrecord/mediverse/biometrics/*.py' 'snow://streamlit/MEDIVERSE360.APP.MEDIVERSE360_APP/versions/live/mediverse/biometrics/' AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
ALTER STREAMLIT MEDIVERSE360.APP.MEDIVERSE360_APP COMMIT;
ALTER STREAMLIT MEDIVERSE360.APP.MEDIVERSE360_APP ADD LIVE VERSION FROM LAST;
