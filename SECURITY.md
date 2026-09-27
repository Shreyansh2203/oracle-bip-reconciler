# Security Policy

## Incident: a real Oracle credential was published in git history

This repository previously committed a **real, working Oracle Fusion BI Publisher
service-account credential** — the `ORACLE_USER` / `ORACLE_PASS` pair that this service
authenticates with — inside a `.env` file. That file was tracked in git and therefore
served by the public GitHub repository. Anyone who cloned, forked or browsed the history
before the purge could read the username and the password in clear text.

What has been done:

1. The `.env` file was removed from the working tree. `.env` and `.env.*` are now
   gitignored, and only `.env.example` (placeholders) is tracked.
2. The history was rewritten with `git filter-repo` to purge the file, and every branch
   was re-pointed at the rewritten commit. `main` and `master` are the same commit.

**The credential must still be rotated. Purging the history does not un-leak it.**

A history rewrite only changes what the repository serves *now*. It does not, and cannot:

- retract the file from any clone made before the rewrite, or from a fork;
- remove the commit objects from GitHub's object store, forks, or any third-party mirror,
  cache or archive that captured them;
- invalidate the credential itself, which is still a valid username and password against
  the Oracle tenant until an administrator changes it.

Treat the credential as compromised. The only thing that actually closes the incident is
rotating the password in Oracle and updating every consumer of it. Anyone holding a
pre-purge clone can still recover the old value from their own `.git` directory.

If you have a clone from before the rewrite, the safest course is to delete it and clone
again. If you must keep it, be aware that `git log` in that clone still contains the
credential, and rotate the password regardless.

## Rotating the credential

Rotation is an Oracle administrator task. This service holds no way to rotate its own
credentials, and no operator of this repository can perform it — it has to be done in the
Oracle Fusion tenant and then propagated to each deployment.

### 1. Reset the password in Oracle Fusion

1. Sign in to the Fusion instance as an administrator.
2. Open **Navigator → Security and Access → Users** (the *Manage Users* task) and search
   for the service account's username. Confirm it is a dedicated integration user and not
   a named employee or supplier account, and record which roles it holds.
3. Open the user record and use **Actions → Reset Password**. Enter a new strong password
   and confirm it. The password is displayed only once — capture it straight into your
   secret store, not into a shell history or a file.
4. Resetting the password also clears a `Locked` account state caused by earlier failed
   signon attempts. Check the **Status** field on the user record is back to `Active`; if
   it is not, reactivate it explicitly.
5. The new password takes effect immediately for new signon attempts, and the account's
   existing sessions are no longer usable. If your instance exposes an explicit
   **Actions → Terminate** on the user record, use it as well so any in-flight session is
   closed without waiting for timeout.

Where a REST-driven reset is preferred, the HCM endpoint
`POST /hcmRestApi/resources/latest/workers/{workerId}/actions/changePassword` with a
`{"newPassword": "..."}` body performs the same change. It requires the worker's
identifying number rather than the username, and is not present on every release, so the
UI path above is the reliable one.

### 2. Revoke anything else bound to that user

The leaked value was a password, but check for the other secrets that commonly hang off a
single integration user and were reachable with the same credential:

- **OAuth clients** — Setup Manager → Identity Domains → Applications, and Security and
  Access → *OAuth* or *Certificates*. Revoke or re-issue any client registered for the
  account.
- **Certificates** — the **Certificates** tab on the user record itself.
- **API keys / shared secrets** held in any secret manager that stores the pair for
  another service.

### 3. Review the account for misuse

Because the credential was public for a period, treat its use history as untrusted:

- Query the signon audit for the account and look for signon attempts or report executions
  you do not recognise:

  ```sql
  select event_date, action, result
    from fnd_signon_audit_all
   where user_name = 'THE_SERVICE_ACCOUNT'
   order by event_date desc;
  ```

  Where that view is not available to your role, use Audit Trail reporting instead
  (Setup Manager → Enterprise Management → Audit Trail) and filter on the same user.
- Check which BI Publisher reports were executed under the account, and confirm each one
  is one of the two reports this service legitimately runs. Any other report path means
  the account was used for something it should not have been.
- If you find unexpected access, treat it as a reportable incident in its own right. A
  password rotation closes the door going forward; it does not undo what was read.

### 4. Update every consumer of the credential

The new password has to reach each place the old one was configured, or the service will
fail closed with a `502`:

| Consumer | Where the value lives |
|---|---|
| Render | Service → Environment, in the blueprint-managed service in `render.yaml` |
| Vercel | Project → Settings → Environment Variables |
| Local development | Your untracked `.env`, copied from `.env.example` |
| CI | Workflow `env:` / secrets, if the value is ever supplied there |

Redeploy after updating. No `.env` should ever be committed; `tests/conftest.py` sets
placeholder values unconditionally so the test suite can never pick up a real credential
from a developer's working copy.

### 5. Verify

- Confirm the new credential works: one `POST /v1/reconcile/batch` against a known-good
  payload returns a reconciled body rather than a `502`.
- Confirm the old password no longer works. Do not test this through this service's logs —
  test it directly against the Fusion signon page or the report endpoint, then discard the
  evidence of the attempt.
- Confirm the account holds only the roles it needs to run the two reports. If it is an
  administrator, reduce it as part of the rotation.

## Reporting a vulnerability

Report suspected vulnerabilities privately through GitHub's
[private vulnerability reporting](https://docs.github.com/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository, which is enabled for `Shreyansh2203/oracle-bip-reconciler`. Please do
not open a public issue for anything that could expose customer or financial data.

Include the reproduction steps, the affected commit, and the impact. There is no formal
response-time commitment; the maintainer aims to acknowledge a report within a few days.

## Scope and hardening

Out of scope for this service, by design:

- **No API key.** Authentication is the caller's or the gateway's responsibility. The
  service exposes a rate limit instead of a shared secret, and must not be exposed
  directly to the public internet.
- **No customer data in issues or pull requests.** The gitignore excludes `Customers.txt`
  and `Real Test Cases/` for this reason.

In scope and enforced:

- Fail-closed CORS by default, with `allow_credentials=False` throughout.
- `ORACLE_PASS` is declared with `repr=False`, so it cannot reach the logs through
  `str(settings)`, `repr(settings)` or a `ValidationError`.
- `defusedxml` parses the SOAP response, so a hostile or malformed Oracle reply cannot
  mount an entity-expansion attack.
- Plain `http://` to a non-loopback `ORACLE_URL` is refused at startup unless
  `ALLOW_INSECURE_ORACLE_HTTP=true` is set explicitly.
- Oracle exceptions, report paths, hostnames and ORA codes are logged server-side and
  never returned to the client; the client receives a fixed `502` message.
- `bandit` and a weekly `pip-audit` scan run in CI. See
  [README.md](README.md#quality-gates) for the full gate list.
