# MANUAL GROUND TRUTH

Files in this folder are **labelled by a human** from the source PDF. They are the only thing the evaluator treats
as correct. **Never copy extractor, mock or dry-run output in here and call it truth.**

## Workflow (all offline, no model)

```
# 1. (optional) get a blank skeleton - it only gives you page/line hints, no answers
python -m hwsets.evaluate --scaffold data/ground_truth/book.json --pdf data/book.pdf --pages 285-294 --limit 5

# 2. read the PDF, fill the skeleton by hand, set "verified": true

# 3. score any extraction JSON against it
python -m hwsets.evaluate --expected data/ground_truth/book.json --actual outputs/book.json [--json report.json]
```

The evaluator refuses files with `"verified": false` or with any `<FILL IN>` left (`--allow-unverified` exists only
to try the tool).

## Format

| key | meaning |
|---|---|
| `document` | PDF file name |
| `verified` | **must be `true`** - a human checked every entry |
| `labelled_by` | who labelled it |
| `sets_complete` | `true` only if EVERY set in the document is listed. Then extra extracted sets count as errors. Default `false`: extra sets are just "not labelled". |
| `sets[].set_number` | required. Compared after trimming/case-folding; `"1"` and `"01"` are different |
| `sets[].description` | optional. `null` means "this set has no description" |
| `sets[].status` | optional: `"active"` or `"not_used"` |
| `sets[].pages` | optional: pages the set occupies (checked against the extracted locations) |
| `sets[].components` | optional. Leave the key out to label only the set, not its rows |
| `sets[].components_complete` | default `true`. Set `false` if you only listed SOME rows, so extra rows are not errors |
| `components[].qty` `description` `catalog_number` `mfr` `finish` `notes` | each optional |

### Unlabelled vs null

* **Key absent** = *unlabelled*: never scored, never counted wrong.
* **`null`** = *labelled as empty*: the extraction must also have `null` there (e.g. a row with no quantity printed).

### Illustrative example - INVENTED DATA, NOT FROM ANY DOCUMENT

```json
{
  "document": "example.pdf",
  "verified": true,
  "labelled_by": "a person",
  "sets_complete": false,
  "sets": [
    {
      "set_number": "X1",
      "status": "active",
      "components": [
        { "qty": 2, "description": "WIDGET HINGE", "catalog_number": "W-100", "mfr": "ACME", "finish": "F1" },
        { "qty": null, "description": "BY OTHERS", "catalog_number": null }
      ]
    },
    { "set_number": "X2", "status": "not_used" }
  ]
}
```

## Matching rules (summary - full text in `src/hwsets/evaluate.py`)

* Sets: normalised exact `set_number`. Near misses (leading zeros / `SET ` prefix) are reported, never matched.
* Components, per matched set, labelled fields only, strict tiers: **A** catalog+description equal, **B** catalog
  equal, **C** description equal. No fuzzy matching. Leftovers are "missing" / "extra".
* Text is compared normalised (case, whitespace, curly quotes/dashes, private-use glyphs); the report shows original values.
* mfr/finish **swaps** are counted separately from ordinary wrong values.
* No single overall score is produced.
