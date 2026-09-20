# Amazon RDS Certificate Authority Trust Store

## Purpose

This directory holds the trusted Amazon RDS Certificate Authority (CA) root and intermediate certificate bundle used by Nexa Care runtime and migration engines to establish fully verified, encrypted TLS connections to Amazon RDS PostgreSQL databases (`verify-full` equivalent).

Connecting to Amazon RDS with `check_hostname=True` and `verify_mode=ssl.CERT_REQUIRED` requires verifying the server certificate against official AWS RDS root certificates.

## Committed Bundle Metadata

- **Filename**: `aws-rds-ca-bundle.pem`
- **Authoritative AWS Source URL**: `https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem`
- **Retrieval Date**: `2026-09-20`
- **SHA-256 Digest**: `e5bb2084ccf45087bda1c9bffdea0eb15ee67f0b91646106e466714f9de3c7e3`
- **File Size**: `165,408 bytes`
- **Certificate Count**: 108 trusted root and intermediate X.509 certificates.

## Public Trust Material, Not Credentials

This PEM file contains only **public root and intermediate CA certificates** published by Amazon Web Services. It contains:
- Zero private keys.
- Zero database credentials or passwords.
- Zero account identifiers or sensitive configuration.

It is safe to commit to the source repository.

## Why the Bundle is Committed

The application container image is immutable and builds from repository source (`Dockerfile`). Baking the authoritative CA bundle into `/app/deploy/ssl/aws-rds-ca-bundle.pem` ensures:
1. The container runtime never performs runtime outbound network requests to download certificates at startup.
2. Startup preflight validates CA bundle availability and certificate structure offline and deterministically.
3. Both the FastAPI application engine and Alembic schema migration runner share the exact same CA trust store.

## Governance and Refresh Procedure

AWS periodically rotates and updates RDS CA certificates (e.g. `rds-ca-rsa2048-g1`, `rds-ca-rsa4096-g1`, `rds-ca-ecc384-g1`). This bundle is **not immutable forever** and must be refreshed under governance when AWS updates root certificates or before current CAs expire:

1. Download the updated bundle from the authoritative AWS PKI trust store:
   ```bash
   curl -sS https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem -o deploy/ssl/aws-rds-ca-bundle.pem
   ```
2. Compute and record the SHA-256 digest:
   ```bash
   sha256sum deploy/ssl/aws-rds-ca-bundle.pem
   ```
3. Verify that `openssl` or Python's `ssl.create_default_context(cafile=...)` parses the certificate chain without error.
4. Update this `README.md` with the new retrieval date and SHA-256 digest.
5. Re-run all database TLS qualification tests (`tests/test_database_tls_contract.py` and preflight test suites).
6. Note: An authoritative regional bundle (e.g., `https://truststore.pki.rds.amazonaws.com/ap-south-1/ap-south-1-bundle.pem`) may alternatively be adopted if deliberately chosen and qualified; do not silently replace or mix bundles without updating documentation and re-running qualification.
