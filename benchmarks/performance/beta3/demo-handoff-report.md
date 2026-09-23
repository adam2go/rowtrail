# RowTrail analysis package

Package `a6ab9b1767784e9fb2d7bc38c6af1e4f` · root `n000`

Included Parquet results can be verified and imported for follow-up queries.
Original recomputation requires all inputs and an explicit recipe run. No SQL or code runs on import.
Checksums detect changed bytes; they do not certify the author, claims or original source freshness.

## Author notes

    Paid orders, excluding bots. Channels normalized to lowercase. All amounts in cents.

## Branch

### n000 (result)

Rows: 4. Payload: n000.parquet.

Quality and provenance (declarations; see manifest for full identity):

    {
      "quality": {
        "accuracy": "exact",
        "coverage": {
          "kind": "complete",
          "scope_ref": "scope_ddd8f112eb2f47898e8588aa604cbce5"
        },
        "final_for_request": true,
        "source_consistency": "immutable_materialized",
        "numeric": {
          "policy": "rowtrail-numeric-v1",
          "other_sql": "engine_semantics",
          "input_provenance": "unknown"
        },
        "lineage": [
          {
            "binding": {
              "result_ref": "res_ea9f496a13784169bce03f8c712bc31d",
              "revision": 1
            },
            "accuracy": "exact",
            "coverage": {
              "kind": "complete",
              "scope_ref": "scope_e3a359abedad4223863b8fd8b8cd5e95"
            },
            "numeric": {
              "policy": "rowtrail-numeric-v1",
              "other_sql": "engine_semantics",
              "input_provenance": "unknown"
            }
          }
        ]
      },
      "verification": {
        "validity": "stored",
        "original_sources": "not_rechecked",
        "parts": "checked_when_read_or_used"
      },
      "provenance": {
        "description": "Revenue and order counts after case normalization and bot exclusion.",
        "origin": "",
        "code": ""
      },
      "parameters": [],
      "dependencies": {
        "t": "n001"
      }
    }

Bounded result preview (presentation.has_more is separate from full payload coverage):

    {
      "schema": [
        {
          "name": "channel",
          "type": "Utf8",
          "nullable": true
        },
        {
          "name": "orders",
          "type": "Int64",
          "nullable": false
        },
        {
          "name": "revenue_cents",
          "type": "Int64",
          "nullable": true
        }
      ],
      "rows": [
        [
          "direct",
          "15585",
          "79495325"
        ],
        [
          "organic",
          "31168",
          "158900110"
        ],
        [
          "paid",
          "15585",
          "79494900"
        ],
        [
          "referral",
          "15584",
          "79445514"
        ]
      ],
      "presentation": {
        "returned_rows": 4,
        "has_more": false
      }
    }

Executed SQL:

    SELECT channel, COUNT(*) orders, SUM(revenue_cents) revenue_cents FROM t GROUP BY channel ORDER BY channel

### n001 (result)

Rows: 77922. Payload: n001.parquet.

Quality and provenance (declarations; see manifest for full identity):

    {
      "quality": {
        "accuracy": "exact",
        "coverage": {
          "kind": "complete",
          "scope_ref": "scope_e3a359abedad4223863b8fd8b8cd5e95"
        },
        "final_for_request": true,
        "source_consistency": "best_effort",
        "numeric": {
          "policy": "rowtrail-numeric-v1",
          "other_sql": "engine_semantics",
          "input_provenance": "unknown"
        },
        "lineage": [
          {
            "binding": {
              "dataset_ref": "ds_3162139b24ed48288561c6dc1a202c72",
              "manifest_ref": "mf_9a971f8907ef414fb928b1d333696c97"
            },
            "accuracy": "exact",
            "coverage": {
              "kind": "complete"
            },
            "numeric": {
              "policy": "unknown"
            }
          }
        ]
      },
      "verification": {
        "validity": "stored",
        "original_sources": "not_rechecked",
        "parts": "checked_when_read_or_used"
      },
      "provenance": {
        "description": "Paid orders excluding bots; money is integer cents.",
        "origin": "",
        "code": ""
      },
      "parameters": [],
      "dependencies": {
        "source": "n002"
      }
    }

Bounded result preview (presentation.has_more is separate from full payload coverage):

    {
      "schema": [
        {
          "name": "order_id",
          "type": "Int64",
          "nullable": true
        },
        {
          "name": "channel",
          "type": "Utf8",
          "nullable": true
        },
        {
          "name": "revenue_cents",
          "type": "Int64",
          "nullable": true
        }
      ],
      "rows": [
        [
          "1",
          "organic",
          "117"
        ],
        [
          "2",
          "paid",
          "134"
        ],
        [
          "3",
          "referral",
          "151"
        ],
        [
          "4",
          "direct",
          "168"
        ],
        [
          "5",
          "organic",
          "185"
        ]
      ],
      "presentation": {
        "returned_rows": 5,
        "has_more": true
      }
    }

Executed SQL:

    SELECT order_id, lower(channel) channel, revenue_cents FROM source WHERE status = 'paid' AND is_bot = 0

### n002 (input)

Rows: not counted. Payload: not included.

Quality and provenance (declarations; see manifest for full identity):

    {
      "quality": null,
      "verification": {
        "validity": "stored",
        "files": "not_rechecked",
        "independent": false
      },
      "provenance": null,
      "parameters": null,
      "dependencies": {}
    }

