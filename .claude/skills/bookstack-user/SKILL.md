---
name: bookstack-user
description: Manage BookStack users and their entitlements (roles and role permissions) through the BookStack REST API with curl — create, list, look up, update, rename, reset passwords of and delete users; grant or revoke roles; create, edit or delete roles and add/remove the system permissions on them (users-manage, content-export, book-view-all, ...); and answer "who has what access" questions. Use this whenever someone wants to do anything with BookStack accounts or access, even if they say "the wiki", "the docs app", "the sample app for the Web Services connector", "give alice editor access", "who are the admins", "offboard bob from bookstack" or "set up test users/entitlements" without naming the API.
---

# BookStack users and entitlements

BookStack (https://www.bookstackapp.com) models access as **users** that hold one or more **roles**; each role carries a list of **system permissions** (strings like `users-manage`, `content-export`, `book-view-all`). So "entitlement" here means a role, and "what can this role do" means its permissions. Everything below goes through the REST API (reference: `<BOOKSTACK_URL>/api/docs`, public copy at https://demo.bookstackapp.com/api/docs).

## Setup

The API needs three values. Look for them in the environment first, then in the repo's `.env` (it is gitignored); ask the user only if they are missing.

| Variable | Example |
|---|---|
| `BOOKSTACK_URL` | `https://<ngrok-domain>/bookstack` or `http://127.0.0.1:8093` (no trailing slash, no `/api`) |
| `BOOKSTACK_TOKEN_ID` | from BookStack: My Account → Access & Security → API Tokens |
| `BOOKSTACK_TOKEN_SECRET` | shown once when the token is created |

The token's owner needs the role permissions **access-api**, **users-manage** and **user-roles-manage** (the Admin role has all three). Never echo the secret or paste it into output.

Define a helper once per shell and check connectivity:

```bash
[ -f .env ] && set -a && . ./.env && set +a
bs() {  # bs METHOD PATH [JSON_BODY]   (works in bash and zsh)
  local method=${@:1:1} endpoint=${@:2:1} body=${@:3:1} data=()
  [ -n "$body" ] && data=(--data "$body")
  curl -sS -g -X "$method" "$BOOKSTACK_URL/api$endpoint" \
    -H "Authorization: Token $BOOKSTACK_TOKEN_ID:$BOOKSTACK_TOKEN_SECRET" \
    -H 'Content-Type: application/json' -H 'Accept: application/json' \
    -H 'ngrok-skip-browser-warning: 1' "${data[@]}" -w '\n%{http_code}\n'
}
bs GET '/users?count=1'
```

The last output line is the HTTP status; the rest is the JSON body. Copy the helper exactly: `-g` stops curl from treating the `[...]` in `filter[email]=` as a URL glob; the body goes through an array because an unquoted `--data` expansion arrives as one argument under zsh; the arguments are read with `${@:N:1}` because this file must not contain dollar-digit references (the skill loader replaces them with the user's words); and the variable isn't called `path`, which in zsh is tied to `PATH`. Shell state may not persist between tool calls, so re-define `bs` (or chain commands with `&&`) in each call. Use `jq` to pick fields; build request bodies with `jq -n` so names and emails are quoted correctly.

Errors come back as `{"error": {"code", "message", "validation"}}`: **422** = validation (read `validation` for the field), **403** = the token's user lacks a permission, **404** = wrong id. `401`/HTML in the body usually means a bad token or a wrong `BOOKSTACK_URL`.

## The two things that go wrong

1. **Arrays are replaced, not merged.** `PUT /users/{id}` with `roles` sets the user's *entire* role list, and `PUT /roles/{id}` with `permissions` sets the role's *entire* permission list. To add or remove one item: GET the current list, change it, PUT the full list. Sending `[]` strips everything. Leave the field out of the PUT entirely if you're not changing it.
2. **Ids are not guessable.** Users and roles are addressed by numeric id. Resolve names/emails to ids with a list query first, and stop and ask if a lookup returns zero or several matches.

## API reference

**Users — `/api/users`**

| Call | Notes |
|---|---|
| `GET /users` | Query: `count` (≤500, default 100), `offset`, `sort=+name` / `-created_at`, `filter[email]=a@b.c`, `filter[name:like]=%ali%`. Returns `{data: [...], total}`. List items have no roles. |
| `GET /users/{id}` | Adds `roles: [{id, display_name}]`. |
| `POST /users` | `name` (required, ≤100), `email` (required, unique), `password` (≥8), `roles: [int]`, `send_invite: bool`, `external_auth_id`, `language`. |
| `PUT /users/{id}` | Same fields, all optional (no `send_invite`). |
| `DELETE /users/{id}` | Optional body `{"migrate_ownership_id": <user id>}` hands their books/pages to someone else. Returns 204. |

**Roles — `/api/roles`**

| Call | Notes |
|---|---|
| `GET /roles` | Items include `users_count`, `permissions_count`, `system_name` (`admin` for the Admin role). |
| `GET /roles/{id}` | Adds `permissions: [string]` and `users: [{id, name, slug}]` — use this for "who has role X". |
| `POST /roles` | `display_name` (required, 3–180), `description`, `mfa_enforced: bool`, `external_auth_id`, `permissions: [string]`. |
| `PUT /roles/{id}` | Same fields, all optional. |
| `DELETE /roles/{id}` | 204. Members simply lose the role. The Admin role and the default registration role cannot be deleted. |

**Permission names.** The authoritative list is the Admin role's permissions, since Admin holds all of them: `bs GET /roles/$(admin role id)` → `.permissions`. Use that list to validate names instead of guessing. Common ones:

| Permission | Grants |
|---|---|
| `access-api` | Use the REST API |
| `users-manage` / `user-roles-manage` | Manage users / manage roles and permissions |
| `settings-manage`, `permissions` | App settings / manage app permissions |
| `content-export`, `content-import` | Export / import content |
| `<item>-<action>-<scope>` | `item` ∈ bookshelf, book, chapter, page, image, attachment, comment; `action` ∈ view, create, update, delete; `scope` ∈ all, own (e.g. `book-view-all`, `page-update-own`) |
| `restrictions-manage-all` / `-own` | Change per-item permissions |
| `templates-manage`, `editor-change`, `receive-notifications` | Templates, switch editor, notifications |

## Recipes

Resolve ids first:

```bash
uid=$(bs GET '/users?filter[email]=alice@example.com' | sed '$d' | jq -r '.data[] | .id')
rid=$(bs GET '/roles?count=500' | sed '$d' | jq -r '.data[] | select(.display_name=="Editor") | .id')
```

(`sed '$d'` drops the status line; check it separately when it matters.) URL-encode filter values with spaces or `+` (e.g. `filter[name:like]=%25smith%25` is `%smith%`).

**Create a user with roles**
```bash
body=$(jq -n --arg n "Alice Ng" --arg e "alice@example.com" --argjson r "[$rid]" \
  '{name:$n, email:$e, roles:$r, send_invite:false, password:"Chang3-me-now!"}')
bs POST /users "$body"
```
Prefer `send_invite: true` (emails a set-password link) when mail is configured; otherwise set a random password and tell the user how to share it.

**Update a user** (only the fields that change): `bs PUT /users/$uid '{"name":"Alice Ng-Smith"}'`. A password reset is `{"password":"..."}`.

**Grant a role** — read, merge, write back:
```bash
cur=$(bs GET /users/$uid | sed '$d' | jq '[.roles[].id]')
bs PUT /users/$uid "$(jq -n --argjson c "$cur" --argjson r $rid '{roles: ($c + [$r] | unique)}')"
```

**Revoke a role**: same, with `{roles: ($c - [$r])}`. If that leaves an empty list, the user has no access at all; say so.

**Show a user's access**: `bs GET /users/$uid` → roles; for each role, `bs GET /roles/<id>` → permissions. Present it as a table (role → permissions).

**Who has role X / who are the admins**: `bs GET /roles/$rid` → `.users`.

**Create a role**: `bs POST /roles "$(jq -n '{display_name:"Book Maintainer", description:"Maintains books", permissions:["book-view-all","book-update-all"]}')"`

**Add/remove a permission on a role**:
```bash
cur=$(bs GET /roles/$rid | sed '$d' | jq '.permissions')
bs PUT /roles/$rid "$(jq -n --argjson c "$cur" '{permissions: ($c + ["content-export"] | unique)}')"   # remove: ($c - ["content-export"])
```

**Delete a user**: `bs DELETE /users/$uid '{"migrate_ownership_id": <admin id>}'` (omit the body to leave their content owner-less). **Delete a role**: `bs DELETE /roles/$rid`.

**Bulk** (e.g. "create these 5 test users"): loop over the inputs, one call each, and collect the status codes; report successes and failures together at the end rather than stopping at the first 422.

## Working safely

- **Confirm before destructive changes**: deleting users or roles, removing the Admin role from anyone, or clearing a role's permissions. State exactly what will happen ("delete user 7 alice@example.com; her 3 books move to Admin") and wait for a yes.
- **Keep at least one admin.** Before revoking the Admin role or deleting a user, check `GET /roles/<admin id>` — if that user is the only member, refuse and explain. Also don't remove the token owner's own `access-api` / `users-manage`, which would lock the API out.
- **Verify every write by reading it back** (GET the user or role) and report the resulting state, not just "done".
- When unsure what a request means (e.g. "editor access" when there's no role called Editor), list the roles and ask rather than inventing one.
