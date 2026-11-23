from tests.conftest import PASSWORD, auth_headers, make_user


def test_setup_flow(client):
    assert client.get("/api/v1/auth/setup-status").json() == {"needs_setup": True}
    r = client.post("/api/v1/auth/setup", json={"email": "Root@Example.com", "password": "short"})
    assert r.status_code == 422
    r = client.post("/api/v1/auth/setup", json={"email": "Root@Example.com", "password": PASSWORD})
    assert r.status_code == 201
    assert r.json()["user"]["role"] == "admin" and r.json()["user"]["email"] == "root@example.com"
    assert "cs_session" in r.cookies
    assert (
        client.post("/api/v1/auth/setup", json={"email": "x@example.com", "password": PASSWORD}).status_code
        == 409
    )
    assert client.get("/api/v1/auth/setup-status").json() == {"needs_setup": False}


def test_requires_authentication(client):
    for path in ("/api/v1/overview", "/api/v1/components", "/api/v1/monitors", "/api/v1/reports/summary"):
        assert client.get(path).status_code == 401


def test_login_and_rate_limit(client, db):
    make_user(db, "u@example.com", "viewer")
    for _ in range(5):
        assert (
            client.post("/api/v1/auth/login", json={"email": "u@example.com", "password": "bad"}).status_code
            == 401
        )
    r = client.post("/api/v1/auth/login", json={"email": "u@example.com", "password": PASSWORD})
    assert r.status_code == 429


def test_cookie_auth_requires_csrf_header_for_writes(client, db):
    make_user(db, "op@example.com", "operator")
    r = client.post("/api/v1/auth/login", json={"email": "op@example.com", "password": PASSWORD})
    assert r.status_code == 200
    assert client.get("/api/v1/auth/me").json()["email"] == "op@example.com"  # cookie
    assert client.post("/api/v1/components", json={"name": "x"}).status_code == 403
    r = client.post("/api/v1/components", json={"name": "x"}, headers={"X-CloudSentinel-Client": "web"})
    assert r.status_code == 201
    client.post("/api/v1/auth/logout")
    client.cookies.clear()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_rbac(client, db, viewer, operator, admin):
    assert client.post("/api/v1/components", json={"name": "a"}, headers=viewer).status_code == 403
    assert client.post("/api/v1/components", json={"name": "a"}, headers=operator).status_code == 201
    assert client.get("/api/v1/users", headers=operator).status_code == 403
    assert client.get("/api/v1/users", headers=admin).status_code == 200
    assert client.put("/api/v1/settings", json={"risk_lookback_days": 3}, headers=operator).status_code == 403
    assert (
        client.post(
            "/api/v1/integrations",
            json={"name": "p", "kind": "prometheus", "config": {"base_url": "http://p:9090"}},
            headers=operator,
        ).status_code
        == 403
    )


def test_user_management_guards(client, db, admin):
    me = client.get("/api/v1/auth/me", headers=admin).json()
    assert client.delete(f"/api/v1/users/{me['id']}", headers=admin).status_code == 409
    assert (
        client.patch(f"/api/v1/users/{me['id']}", json={"role": "viewer"}, headers=admin).status_code == 409
    )
    r = client.post(
        "/api/v1/users",
        json={"email": "new@example.com", "password": PASSWORD, "role": "operator"},
        headers=admin,
    )
    assert r.status_code == 201
    assert (
        client.post(
            "/api/v1/users", json={"email": "new@example.com", "password": PASSWORD}, headers=admin
        ).status_code
        == 409
    )
    uid = r.json()["id"]
    assert (
        client.patch(f"/api/v1/users/{uid}", json={"is_active": False}, headers=admin).json()["is_active"]
        is False
    )
    assert (
        client.post("/api/v1/auth/login", json={"email": "new@example.com", "password": PASSWORD}).status_code
        == 401
    )
    assert client.delete(f"/api/v1/users/{uid}", headers=admin).status_code == 204


def test_password_change(client, db):
    make_user(db, "p@example.com", "viewer")
    h = auth_headers(client, "p@example.com")
    assert (
        client.post(
            "/api/v1/auth/password",
            json={"current_password": "nope", "new_password": "another-pass-1"},
            headers=h,
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/v1/auth/password",
            json={"current_password": PASSWORD, "new_password": "another-pass-1"},
            headers=h,
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "p@example.com", "password": "another-pass-1"}
        ).status_code
        == 200
    )


def test_probes_and_openapi(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"
    spec = client.get("/api/openapi.json").json()
    assert "/api/v1/monitors" in spec["paths"]
    assert client.get("/healthz").headers["x-content-type-options"] == "nosniff"


def test_session_cookie_secure_flag_follows_scheme(client, db):
    make_user(db, "c@example.com", "viewer")
    body = {"email": "c@example.com", "password": PASSWORD}
    plain = client.post("/api/v1/auth/login", json=body)
    assert "secure" not in plain.headers["set-cookie"].lower()
    assert "httponly" in plain.headers["set-cookie"].lower()
    assert "samesite=strict" in plain.headers["set-cookie"].lower()
    tls = client.post("https://testserver/api/v1/auth/login", json=body)
    assert "secure" in tls.headers["set-cookie"].lower()
