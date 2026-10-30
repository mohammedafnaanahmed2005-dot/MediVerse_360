-- Real OTP delivery by email (no code is ever shown on screen).
-- Run as ACCOUNTADMIN once. SYSTEM$SEND_EMAIL only delivers to email addresses that are
-- VERIFIED on a Snowflake user in this account (Snowsight > profile > verify email).
USE ROLE ACCOUNTADMIN;
CREATE NOTIFICATION INTEGRATION IF NOT EXISTS MV_EMAIL_INT TYPE = EMAIL ENABLED = TRUE;
GRANT USAGE ON INTEGRATION MV_EMAIL_INT TO ROLE MV_APP_OWNER;
