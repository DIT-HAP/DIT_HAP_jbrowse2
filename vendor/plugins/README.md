# Vendored JBrowse plugins

Third-party plugin bundles, committed here rather than loaded from a CDN or from
`node_modules`. `scripts/99_assemble_site.sh` copies this directory (and the
repository's own `plugins/`) into `site/plugins/`, and config.json's `plugins`
array names them by URL.

Nothing in here is modified. If one of these needs a behaviour change, add a
plugin of our own under `plugins/` that extends it through an extension point —
see `plugins/gwas-chrome.js` for the pattern and the reasoning.

## jbrowse-plugin-gwas.umd.js

| | |
| --- | --- |
| package | [`jbrowse-plugin-gwas`](https://www.npmjs.com/package/jbrowse-plugin-gwas) |
| version | **2.1.6** (2026-03-14) |
| upstream | <https://github.com/GMOD/jbrowse-plugin-gwas> (redirects from `cmdcolin/…`) |
| file in the tarball | `dist/jbrowse-plugin-gwas.umd.production.min.js` |
| SHA-256 | `0b967c4686fb7c341c200dcdb381a161932e6736c2233898a636b3a6a797e517` |
| tarball SHA-256 | `45050d34cac6113590f882aae08967afd3f54a50a024a636d630b0d9e5a05bbd` |
| built against | `@jbrowse/core ^4.1.14` |

Fetched with:

```bash
curl -sSL -o gwas-2.1.6.tgz \
  https://registry.npmjs.org/jbrowse-plugin-gwas/-/jbrowse-plugin-gwas-2.1.6.tgz
tar -xzf gwas-2.1.6.tgz
cp package/dist/jbrowse-plugin-gwas.umd.production.min.js \
   vendor/plugins/jbrowse-plugin-gwas.umd.js
```

The only change is the filename: the published name carries `.production.min`,
which says nothing a reader of this directory needs. The bytes are unmodified,
and the SHA-256 above is of the file as committed — `cmp` it against a fresh
tarball to check.

**2.1.6 is the newest published version, and `npm dist-tags` agrees
(`latest: 2.1.6`). It is not the highest version number.** 2.2.0 was published
2025-11-18, between 2.1.1 and 2.1.2, and the 2.1.x line continued past it. 2.2.0
also targets `@jbrowse/core ^3.3.0` and will not load in JBrowse 4.3.0 at all —
`TypeError: Class extends value undefined is not a constructor or null`, because
it extends `WigglePlugin.exports.WiggleBaseRenderer`, a name 4.3.0 does not
export. Check the publish date, not the version number, before bumping this.
The full story is in the README under "Why the plugin is not simply upgraded".
