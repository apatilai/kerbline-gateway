# Security boundaries and reporting

Kerbline is an engineering pilot. Its tests do not constitute a security certification or approval for production use.

## Keep private material out of Git

Never commit `.local/`, environment files, bearer tokens, service-account keys, database passwords, console credentials, runtime SQLite databases, customer observations or raw prompts. Generated evidence under `tmp/` and `output/` and customer-console data are ignored by default. Review staged files before every push; an ignore rule does not remove a previously tracked secret.

Local credential files use mode `0600`. Production installations should use customer-controlled secret management, workload identity, TLS, network restrictions and appropriate audit retention. The local Docker operator is privileged; the restricted PostgreSQL role does not constrain an administrator with Docker access.

If a credential is exposed, revoke or rotate it in its issuing system and remove it from repository history as appropriate. Deleting the current file alone does not revoke the credential.

## Production boundaries

The local PostgreSQL gateway/adapter binds to loopback and is not privately connected to GCP. Do not expose the local console, Docker socket or database directly to public networks. Model release, arbitrary generated code and business writes remain disabled for real inventory workflows.

Positive field projection is not general DLP or anonymization. The pilot does not supply immutable audit retention, production HA/DR, general enterprise SSO onboarding or compliance certification. See the root README for the current implemented scope.

## Report a concern

Share security findings privately with the repository owner through an agreed private channel. Include a minimal reproduction with synthetic data, affected code paths and expected versus observed behavior. Do not place credentials, customer records or exploitable private infrastructure details in public issues or pull requests.
