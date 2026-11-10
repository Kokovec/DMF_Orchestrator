#!/usr/bin/env python3
"""Refuse viewers the API reads that carry RTMP stream keys and SRT passphrases.

usage: operator-secret-reads.py <switchframe/server>

server/operator/middleware.go lets every GET through whatever the caller's
role. Three reads return secrets:

    GET /api/output/destinations       full RTMP URLs and SRT passphrases
    GET /api/output/destinations/{id}  the same, for one destination
    GET /api/sync/state-snapshot       full RTMP URLs (?admin=true skips its lock)

A token that identifies a registered operator whose role can't command output
now gets 403 on those. Anything else goes on as before: the session API token
(the reconciler and the SSO bridge) identifies no operator, and the auth
middleware has already rejected unknown tokens.

The browser UI never lists destinations. It does read GET /api/debug/snapshot
for its stats panel, so viewers keep that route and its destinations are
redacted for everyone instead: nothing reads a secret from a debug dump.
"""
import pathlib
import sys

server = pathlib.Path(sys.argv[1])
middleware = server / "operator" / "middleware.go"
manager = server / "output" / "manager.go"

text = middleware.read_text()
if "isSecretRead" in text:
    print("operator-secret-reads: already patched; skipping")
    sys.exit(0)

GET_PASS = """			// 2. GET requests always pass through (read-only).
			if r.Method == http.MethodGet {
				next.ServeHTTP(w, r)
				return
			}
"""
if text.count(GET_PASS) != 1:
    sys.exit("operator-secret-reads: GET pass-through in middleware.go not as expected; has upstream changed?")
text = text.replace(GET_PASS, """			// DMF patch: reads that return stream keys and passphrases.
			if r.Method == http.MethodGet && isSecretRead(r.URL.Path) {
				if op, err := store.GetByToken(ExtractBearerToken(r)); err == nil && !CanCommand(op.Role, SubsystemOutput) {
					httperr.Write(w, http.StatusForbidden, "role '"+string(op.Role)+"' cannot read output secrets")
					return
				}
			}

""" + GET_PASS)
ANCHOR = "// NewMiddleware creates HTTP middleware that enforces operator role permissions"
if text.count(ANCHOR) != 1:
    sys.exit("operator-secret-reads: NewMiddleware comment not found; has upstream changed?")
text = text.replace(ANCHOR, """// isSecretRead reports whether a GET returns RTMP stream keys or SRT
// passphrases. DMF patch.
func isSecretRead(path string) bool {
	normalized := normalizeAPIPath(path)
	return normalized == "/api/output/destinations" ||
		strings.HasPrefix(normalized, "/api/output/destinations/") ||
		normalized == "/api/sync/state-snapshot"
}

""" + ANCHOR)
middleware.write_text(text)

(server / "operator" / "secret_reads_test.go").write_text('''package operator

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSecretRead(t *testing.T) {
	store, _ := NewStore(tempStorePath(t))
	sm := NewSessionManager()
	defer sm.Close()
	director, _ := store.Register("Director", RoleDirector)
	viewer, _ := store.Register("Viewer", RoleViewer)
	audio, _ := store.Register("Audio", RoleAudio)

	handler := NewMiddleware(store, sm)(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	get := func(path, token string) int {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		req.Header.Set("Authorization", "Bearer "+token)
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		return rec.Code
	}

	secret := []string{
		"/api/output/destinations",
		"/api/output/destinations/abc",
		"/api/v1/output/destinations",
		"/api/sync/state-snapshot",
		"/api/sync/state-snapshot?admin=true",
	}
	for _, path := range secret {
		require.Equal(t, http.StatusForbidden, get(path, viewer.Token), "viewer %s", path)
		require.Equal(t, http.StatusForbidden, get(path, audio.Token), "audio %s", path)
		require.Equal(t, http.StatusOK, get(path, director.Token), "director %s", path)
		require.Equal(t, http.StatusOK, get(path, "the-session-api-token"), "api token %s", path)
	}
	require.Equal(t, http.StatusOK, get("/api/switch/state", viewer.Token))
	require.Equal(t, http.StatusOK, get("/api/debug/snapshot", viewer.Token))
}
''')

text = manager.read_text()
DEBUG = """	if dests := m.ListDestinations(); len(dests) > 0 {
		snap["destinations"] = dests
	}
"""
if text.count(DEBUG) != 1:
    sys.exit("operator-secret-reads: DebugSnapshot in manager.go not as expected; has upstream changed?")
text = text.replace(DEBUG, """	if dests := m.ListDestinations(); len(dests) > 0 {
		snap["destinations"] = redactDestinations(dests) // DMF patch
	}
""")
manager.write_text(text)

(server / "output" / "destination_redact.go").write_text('''package output

import "github.com/zsiec/switchframe/server/internal"

// redactDestinations removes stream keys and passphrases from statuses that go
// into a debug dump, which any operator can read. ListDestinations returns
// copies, so the configs used to send are untouched. DMF patch.
func redactDestinations(dests []DestinationStatus) []DestinationStatus {
	for i := range dests {
		dests[i].Config.URL = internal.MaskDestinationURL(dests[i].Config.URL)
		dests[i].Config.Passphrase = ""
	}
	return dests
}
''')

(server / "output" / "destination_redact_test.go").write_text('''package output

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestRedactDestinations(t *testing.T) {
	got := redactDestinations([]DestinationStatus{
		{Config: DestinationConfig{URL: "rtmp://host:1935/live/key"}},
		{Config: DestinationConfig{Passphrase: "secretsecret"}},
	})
	require.Equal(t, "rtmp://host:1935/****", got[0].Config.URL)
	require.Empty(t, got[1].Config.Passphrase)
	require.Empty(t, got[1].Config.URL)
}
''')
print("operator-secret-reads: patched")
