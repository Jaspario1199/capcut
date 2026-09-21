# Example: fixture template, fixture footage

```bash
capcut-recreate manifest tests/fixtures/template -o /tmp/manifest.json
capcut-recreate index /tmp/staging tests/fixtures/footage/*.mp4 -o /tmp/footage.json
capcut-recreate check /tmp/manifest.json /tmp/footage.json examples/plan.json
mkdir -p /tmp/store
capcut-recreate apply /tmp/manifest.json /tmp/footage.json examples/plan.json --store /tmp/store
capcut-recreate diff tests/fixtures/template /tmp/store/beach-v1
```

`plan.json` fills the two replaceable media slots and the one replaceable
text slot of the fixture. Clip ids come from the footage index: files are
indexed in sorted filename order, so `c00` is `beach_wide.mp4` and `c02` is
`other_short2s.mp4`.
