# XML contract

Schema: [schemas/dmf-workload-v1.xsd](../../schemas/dmf-workload-v1.xsd), namespace
`urn:dmf:workload:v1`. Example: [examples/studio-a.workload.xml](../../examples/studio-a.workload.xml),
a copy of the toolkit's `scripts/fixtures/studio-a.workload.xml`. It's real output, not hand-written:
when the toolkit's output for it changes, copy it here again.

The XML is a lossless rendering of the toolkit's published JSON (`toPublished`), plus the additions
in [03-toolkit-spec.md](03-toolkit-spec.md). Canvas layout isn't part of the published JSON. Nor
are `flowOptions`, which `toPublished` drops because they're already resolved into `flows`.

Anything the XML can't carry is a publish problem, so the JSON route, the XML route and the canvas
agree on whether a revision is publishable.

## Mapping

| Published JSON | XML |
|---|---|
| `apiVersion`, `metadata.{id,uid,name,revision,libraryVersion}` | attributes of `<workload>`, with `schemaVersion="1"` |
| `metadata.description` | `<description>` |
| `spec.defaults` | `<defaults>`; a `Rational {num, den}` becomes `…Num`, `…Den`; a missing `den` is written as 1 |
| `spec.access.roles` | `<access><role name><member>` |
| `spec.domains[]` | `<domains><domain id>` |
| `functions[].{id,type,typeVersion,name,domain}` | attributes of `<function>` |
| `functions[].inputs`, `.outputs` (block → copies) | `<inputs>`/`<outputs>` with `<use block copies>`, zero counts included |
| `functions[].features` (block → boolean) | `<features><feature block enabled>` |
| `functions[].parameters`, `.settings` | `<parameters>`/`<settings>` with `<value path type>`, below |
| `.control`, `.dependencies`, `.resources`, `.expectations`, `.placement`, `.discovery` | same-named elements |
| `spec.externals[]` | `<externals><external>` |
| `spec.flows[]` | `<flows><flow>`, with `<reader>`, `<format>`, `<discovery>` |
| `spec.wires[]` | `<wires><wire from to>` |

## Generic values

Each leaf of the JSON tree is one element:

```xml
<value path="engine.engine.rate" type="string">29.97</value>
<value path="srt-src_2.srt.latencyMs" type="integer">200</value>
<value path="srt-out.srt.passphrase" type="secret">venue-srt</value>
<value path="vendorThing.items[0].enabled" type="boolean">true</value>
```

| `type` | Content | JSON |
|---|---|---|
| `string` | text, XML-escaped; empty allowed | string |
| `integer` | `-?[0-9]+` | number |
| `number` | decimal, no exponent | number |
| `boolean` | `true` or `false` | boolean |
| `secret` | the secret's name | `{"$secret": name}` |
| `null` | empty | `null` |
| `emptyObject` / `emptyArray` | empty | `{}` / `[]` |

Rules:
- A key that doesn't match `[A-Za-z0-9_-]+` can't be encoded. The toolkit refuses to publish (HTTP 422) and names the key.
- Secret values never appear in the XML. An object with any key beside `$secret` is not a secret reference.
- A missing `<value>` means "not set". The compiler adds no flag or API call for it, so the product's default applies.

## Serialisation (toolkit)

Output must be deterministic, so the same revision gives identical bytes:
- UTF-8, XML declaration, 2-space indent, LF line endings, trailing newline, no DOCTYPE, no comments.
- Element order follows the XSD. Functions, externals, flows, wires and values keep the published document's order.
- Escape `& < > " '`. Also write CR as `&#13;` everywhere, and tab and LF as `&#9;` and `&#10;` inside attributes. Otherwise parsers normalise them and the round trip isn't lossless.
- Refuse characters XML 1.0 can't carry, such as most control characters; XML has no escape for these.
- An `xs:token` attribute (`domain`, `type`, `kind` and others) must already be in normal form: no leading, trailing or repeated spaces, and no tab or newline. Otherwise a reader collapses it and changes the value, so the toolkit refuses it. Free text uses `xs:string`.
- Ids (workload, function, external) match the schema's `Id`: `^[a-z0-9]([-a-z0-9]{0,40}[a-z0-9])?$`. This fits Kubernetes names as `wl-<id>` and this repo's plan check. The toolkit enforces it when saving, not only when serialising.
- `historyMs` is at least 1, in the toolkit and in the schema.

## Reading (compiler)

In this order, stopping at the first failure:
1. Refuse any document containing `<!DOCTYPE` or `<!ENTITY`, before parsing.
2. Validate against the XSD using the `xmlschema` package.
3. Parse, then apply the semantic checks in [04-orchestrator-spec.md](04-orchestrator-spec.md).

Readers must convert `<value>` trees back to JSON with the table above. Both repos test that
round trip on the same fixtures.

## Versioning

- `schemaVersion` and the namespace change only for an incompatible change, with a new file: `dmf-workload-v2.xsd`.
- New module parameters, modules and library features don't change the schema.
- The compiler accepts `schemaVersion=1` and the module versions it knows. Any other version is refused with a message naming it.
- `libraryVersion` is informational: the compiler records it and doesn't check it, so a library bump alone never blocks a deployment. Module versions are what it gates on. A library change that alters a module's meaning must bump that module's version. Narrowing which values a module accepts doesn't alter meaning, so it needs no bump while no saved workload uses that version (toolkit R3.5). Once one does, any change needs a new version.
- `schemas/dmf-workload-v1.xsd` here is the source of truth. The toolkit's copy must be byte-identical; its tests compare a hash recorded in the toolkit.

## Flow identity

`uid = metadata.uid ?? uuidv5(9e6d3b0a-7c1f-5d4e-8a2b-1f0c3d5e7a9b, metadata.id)`
`flow.id = uuidv5(uid, "<function>/<port>")`

Test vector: workload id `t1`, writer `sw/mxl-out.video` gives
`11d120ff-ba57-5632-bc10-0a2c5e3ec600`. The compiler uses the published IDs and never recomputes them.

## Source keys

SwitchFrame's API addresses sources by key. Agents must not assume more than this:

| Source | Key |
|---|---|
| MXL | `mxl:<video flow id>` (as the current chart uses it) |
| SRT listener | `srt:<streamId>`. The product strips `/` and `live/` and sanitises, so the toolkit and compiler require `^[A-Za-z0-9_-]{1,64}$` |
| SRT caller | **Runtime only.** `POST /api/sources` returns it and its API doc shows `srt-cam1-ab12cd34`. Find it by matching `streamID` in state; never derive it |
