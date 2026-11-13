-- The app must NOT be a superuser, otherwise PostgreSQL skips row-level security.
CREATE ROLE dash LOGIN PASSWORD 'dashpass';
CREATE DATABASE smartfleet OWNER dash;
