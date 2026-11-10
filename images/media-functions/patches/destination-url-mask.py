#!/usr/bin/env python3
"""Keep RTMP stream keys out of ControlRoomState.

usage: destination-url-mask.py <switchframe/server>

server/cmd/switchframe/app_state.go copies each output destination's full URL
into ControlRoomState (GET /api/switch/state and every operator's state
broadcast, viewers included). An RTMP stream key is the last path segment of
that URL. This patch leaves only the scheme and host in the state field:

    rtmp://host:1935/live/key  ->  rtmp://host:1935/****

rtmpMaskURL (server/output/types.go) is not reused: it only masks after a
fourth slash, so rtmp://host/key would come through whole. The real URL stays
in the destination's config, which is what is used to send.
"""
import pathlib
import sys

server = pathlib.Path(sys.argv[1])
mask = server / "internal" / "destination_url_mask.go"
if mask.exists():
    print("destination-url-mask: already patched; skipping")
    sys.exit(0)

mask.write_text('''package internal

import "net/url"

// MaskDestinationURL reduces a destination URL to its scheme and host, for
// state shown to operators: an RTMP stream key is part of the URL's path.
// DMF patch. Anything that doesn't parse as scheme://host is masked entirely.
func MaskDestinationURL(raw string) string {
	if raw == "" {
		return ""
	}
	u, err := url.Parse(raw)
	if err != nil || u.Scheme == "" || u.Host == "" {
		return "****"
	}
	return u.Scheme + "://" + u.Host + "/****"
}
''')

(server / "internal" / "destination_url_mask_test.go").write_text('''package internal

import "testing"

func TestMaskDestinationURL(t *testing.T) {
	for raw, want := range map[string]string{
		"":                               "",
		"rtmp://host/key":                "rtmp://host/****",
		"rtmp://host:1935/live/key":      "rtmp://host:1935/****",
		"rtmps://user:pass@host/app/key": "rtmps://host/****",
		"rtmp://host":                    "rtmp://host/****",
		"not a url":                      "****",
		"rtmp://[::1]:1935/live/key":     "rtmp://[::1]:1935/****",
	} {
		if got := MaskDestinationURL(raw); got != want {
			t.Errorf("MaskDestinationURL(%q) = %q, want %q", raw, got, want)
		}
	}
}
''')

path = server / "cmd" / "switchframe" / "app_state.go"
src = path.read_text()
old = "\t\t\t\tURL:            d.Config.URL,\n"
new = "\t\t\t\tURL:            internal.MaskDestinationURL(d.Config.URL), // DMF patch: no stream key in state\n"
if src.count(old) != 1:
    sys.exit("destination-url-mask: app_state.go: destination state code changed upstream")
path.write_text(src.replace(old, new))
print("destination-url-mask: patched")
