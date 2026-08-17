# Notes on the auth PR (#5, `4df841a`)

Review notes for whoever owns `api/auth/login.py` and `api/shared/auth.py`.

The design is sound and I am not proposing changes to it: HS256, 12-hour TTL, signing
secret from Key Vault with a **loud** failure when absent, `password_hash` on `Employees`
rather than a second identity table, identical error text for unknown-email and
wrong-password so accounts cannot be enumerated, `require_manager()` failing closed on an
unrecognised role. All of that is right.

What follows is three things that stop it working end to end today, and one missing claim.

---

## Three blockers to a working sign-in

### 1. `JWT_SIGNING_SECRET` is never provisioned — `/login` returns 500

`shared/auth.py` raises `RuntimeError("JWT_SIGNING_SECRET is not set")` when the variable
is missing, which is correct. But nothing sets it:

- `infra/modules/keyvault/main.tf` defines three secrets — `sql-connection-string`,
  `openai-api-key`, `sql-password`. There is no `jwt-signing-secret`.
- `infra/modules/functions/main.tf` `app_settings` has no `JWT_SIGNING_SECRET` entry.

So every call to `/login` fails at `_secret()` before it reaches the database. The fix
follows the pattern already used for `SQL_PASSWORD` in the same file:

```hcl
# infra/modules/keyvault/main.tf
resource "azurerm_key_vault_secret" "jwt_signing_secret" {
  name         = "jwt-signing-secret"
  value        = var.jwt_signing_secret      # openssl rand -hex 32
  key_vault_id = azurerm_key_vault.main.id
}

# infra/modules/functions/main.tf — app_settings
"JWT_SIGNING_SECRET" = "@Microsoft.KeyVault(SecretUri=${var.key_vault_uri}secrets/jwt-signing-secret/)"
```

One thing to get right: this secret must be **stable**. Regenerating it on every
`terraform apply` signs every user out on every deploy.

### 2. No employee has a password — every login returns 401

`012_add_auth.sql` adds `password_hash` as nullable, which is the correct migration.
But nothing in the repository ever *writes* a hash — `login.py` only reads it, and
returns 401 when it is `NULL`, which is currently every row.

So even with the secret fixed, no one can sign in. This needs either:

- a one-off admin backfill script that bcrypt-hashes a password per seeded employee, or
- a first-login "set your password" flow.

The migration comment already anticipates this ("backfill via a one-off admin script").
Whoever writes it: hash with `bcrypt.hashpw(pw.encode(), bcrypt.gensalt())`, and do not
put the plaintext in the repo, in a workflow variable, or in the script's default.

### 3. `api/` has never been deployed

The repository has three workflows — `terraform.yml`, `migrate-database.yml`,
`tests.yml`. None of them deploy the Function App code. `login.py` exists in git and
does not exist in Azure.

This is Track G. Worth knowing that blockers 1 and 2 cannot be verified until it lands.

---

## One missing claim: `role_code`

The schema keeps two different "roles" in two different columns, because they answer
different questions:

| Column | Question | Values | In the token? |
|---|---|---|---|
| `Roles.access_role` | What may this person **do**? | employee, manager, director, admin, executive | yes |
| `Roles.title` → role code | What is this person **taught**? | SDE1, SDE2, SWE_MANAGER, SEC_ANALYST, ALL | **no** |

`create_token()` carries `access_role`, which is what `require_manager()` needs. But the
training role never enters the token.

Every question in the bank carries a `role_scope`, inherited from the blob container its
source document came from (`src/quizgen/sources.py`, `models.py`). Serving the right
training means filtering on that scope — and the value to filter by is the caller's
*training* role, not their permission tier. Without the claim, any endpoint that serves
training has to re-query the database for something already known at login.

Do not infer one from the other: an employee whose title contains "lead" is not a
manager, and a `SWE_MANAGER` training role does not by itself grant `manager` access.

The join is already in `login.py`'s query — it only needs one more column:

```sql
SELECT e.id, e.email, e.company_id, e.password_hash, r.access_role, r.title
    FROM dbo.Employees e
    LEFT JOIN dbo.Roles r ON r.id = e.role_id
    WHERE e.email = ?
```

```python
# shared/auth.py
payload = {..., "role_code": role_code or "ALL"}

@dataclass
class Identity:
    ...
    role_code: str = "ALL"
```

`"ALL"` is the safe default: it serves company-wide training and nothing role-specific,
so a missing or unmapped role under-serves rather than leaking another role's material.

**`manager_id` is worth carrying for the same reason.** `Employees.manager_id`
(migration 001) answers "who reports to me" — needed for the manager's team view, for
scoping which roles a manager may upload for, and for deciding who may see whose Q Score.

---

## And the thing to do next: nothing is actually protected

`get_current_employee()` is defined in `shared/auth.py` and called nowhere.
`function_app.py` still resolves identity through `_caller_id()` at lines 182, 268, 376
and 559.

`_caller_id()` falls back to the `x-learner-id` header and, failing that, to the literal
string `"demo-learner"`. That fallback is the part worth attention: a client that sends
no header at all does not get an error — it silently becomes `demo-learner`. Every user
would share one identity and one quiz history, and nothing anywhere would report a
problem.

So the login endpoint is currently a front door on a house whose other doors are open.
Moving those four call sites to `get_current_employee()` + `require_manager()` is what
makes the PR mean something, and it is a small change now that the helpers exist.

Two call sites deserve `require_manager()` rather than just authentication: document
upload and role editing both change what an entire role is taught and certified against.
