# Vendored front-end libraries

Pinned and kept here so the console page makes no external network requests.

| Package | Version | From | npm integrity (of the tarball) |
|---|---|---|---|
| `@xterm/xterm` | 6.0.0 | `lib/xterm.js`, `css/xterm.css`, `LICENSE` | `sha512-TQwDdQGtwwDt+2cgKDLn0IRaSxYu1tSUjgKarSDkUM0ZNiSRXFpjxEsvc/Zgc5kq5omJ+V0a8/kIM2WD3sMOYg==` |
| `@xterm/addon-fit` | 0.11.0 | `lib/addon-fit.js`, `LICENSE` | `sha512-jYcgT6xtVYhnhgxh3QgYDnnNMYTcf8ElbxxFzX0IZo+vabQqSPAjC3c1wJrKB5E19VwQei89QCiZZP86DCPF7g==` |

Both are MIT licensed (each directory's `LICENSE`). The only change: the trailing
`//# sourceMappingURL=` line is removed from each `.js` file, because the source maps aren't
vendored and a browser's developer tools would otherwise request them.

To update: download `https://registry.npmjs.org/@xterm/xterm/-/xterm-<version>.tgz`, check its
sha512 against `npm view @xterm/xterm@<version> dist.integrity`, copy the same files into a new
directory named for the version, and change the paths in `../index.html`.
