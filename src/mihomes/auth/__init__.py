"""Authentication — Google OIDC, server-side sessions, CSRF (G12 · §6 Step 12).

    auth/oidc.py      IdentityProvider Protocol + GoogleOIDCProvider + users upsert
    auth/sessions.py  server-side session store (only the hash reaches the database)
    auth/csrf.py      double-submit token

The token *verification* is SPEC-001's `landing/oauth.py`, reused rather than reimplemented — see
`oidc.py` for why a second verifier would be the wrong call.
"""
