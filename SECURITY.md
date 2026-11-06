# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 4.x | ✅ |
| < 4.0 | ❌ |

## Reporting a Vulnerability

If you discover a security vulnerability in Oracle-BIP-Reconciler, please report it responsibly.

**Do NOT** open a public GitHub issue for security vulnerabilities.

### How to Report

1. Email: [main.doga@gmail.com](mailto:main.doga@gmail.com) with subject "Oracle-BIP-Reconciler Security Vulnerability"
2. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

### Response Timeline

- **Acknowledgment**: Within 48 hours
- **Initial assessment**: Within 7 days
- **Fix release**: Within 14 days (critical), 30 days (non-critical)

### Security Measures

- **Fail-closed authentication**: `ALLOW_UNAUTHENTICATED_ACCESS=false` by default
- **Authenticating proxy required**: Service must run behind OAuth2/OIDC/IAP proxy
- **SAST**: Bandit scans on every push
- **Dependency audit**: pip-audit in CI
- **Dead code detection**: Vulture in CI
- **Secrets never logged**: JsonFormatter scrubs sensitive fields
- **Rate limiting**: slowapi middleware
- **Request body limits**: RequestBodyLimitMiddleware
- **Secrets management**: No credentials in source code

### Authentication

This service **must** run behind an authenticating proxy (OAuth2/OIDC/IAP). Direct access without authentication is not supported and is blocked by default.

Set `ALLOW_UNAUTHENTICATED_ACCESS=true` only for local development.

### Disclosure Policy

- Vulnerabilities are disclosed after a fix is released
- Credit is given to the reporter (unless they prefer anonymity)
- Public disclosure timeline: 90 days after fix release

## Security Contacts

- **Maintainer**: Shreyansh Srivastava ([@Shreyansh2203](https://github.com/Shreyansh2203))
- **Email**: main.doga@gmail.com