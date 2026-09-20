//! Subprocess crash injection: hooks are absent from production binaries.
use crate::{db::Db, model::Part};
use arrow::{
    array::Int64Array,
    datatypes::{DataType, Field, Schema},
    record_batch::RecordBatch,
};
use rowtrail_contracts::{DatasetBinding, Request};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{path::PathBuf, sync::Arc};

pub fn fault(point: &str) {
    if std::env::var("ROWTRAIL_TEST_COMMIT_FAULT").as_deref() == Ok(point) {
        // No destructors, rollback or graceful cancellation: simulate lost power.
        std::process::exit(86);
    }
}

#[test]
fn fault_child() {
    let Ok(root) = std::env::var("ROWTRAIL_TEST_COMMIT_WORKSPACE") else {
        return;
    };
    let workspace = PathBuf::from(root);
    let prepared = std::env::var("ROWTRAIL_TEST_PREPARE").is_ok();
    let rt = tokio::runtime::Runtime::new().unwrap();
    rt.block_on(async {
        for dir in ["store", "staging", "spill", "logs"] {
            std::fs::create_dir_all(workspace.join(dir)).unwrap();
        }
        let db = Arc::new(Db::open(&workspace).unwrap());
        let response = crate::api::dispatch(
            db.clone(),
            Request::new(
                "query",
                json!({"bindings":{},"sql":"SELECT 7","execution":{"wait_ms":0}}),
            ),
        )
        .await;
        assert!(response.ok, "{response:?}");
        let mut spec = db.queued().unwrap().unwrap();
        if prepared {
            spec.prepared = Some(DatasetBinding {
                dataset_ref: "ds_test".into(),
                manifest_ref: "mf_test".into(),
            });
            spec.query.as_mut().unwrap().execution.preview = "none".into();
            db.conn
                .lock()
                .unwrap()
                .execute(
                    "UPDATE jobs SET spec=? WHERE id=?",
                    rusqlite::params![serde_json::to_string(&spec).unwrap(), spec.job_id],
                )
                .unwrap();
        }
        let schema = Schema::new(vec![Field::new("x", DataType::Int64, false)]);
        let batch = RecordBatch::try_new(
            Arc::new(schema.clone()),
            vec![Arc::new(Int64Array::from(vec![7]))],
        )
        .unwrap();
        db.set_schema(&spec, &schema).unwrap();
        let dir = workspace.join("staging").join(&spec.attempt);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join(if prepared {
            "000000000000.parquet"
        } else {
            "000000000000.arrow"
        });
        let file = std::fs::File::create(&path).unwrap();
        if prepared {
            let mut writer =
                parquet::arrow::ArrowWriter::try_new(file, Arc::new(schema.clone()), None).unwrap();
            writer.write(&batch).unwrap();
            writer.into_inner().unwrap().sync_all().unwrap();
        } else {
            let mut writer = arrow::ipc::writer::FileWriter::try_new(file, &schema).unwrap();
            writer.write(&batch).unwrap();
            writer.finish().unwrap();
            writer.into_inner().unwrap().sync_all().unwrap();
        }
        let bytes = std::fs::read(&path).unwrap();
        db.publish(
            &spec,
            &Part {
                seq: 0,
                path,
                bytes: bytes.len() as u64,
                rows: 1,
                checksum: hex::encode(Sha256::digest(bytes)),
                schema,
                checkpoint: None,
            },
        )
        .unwrap();
        db.finish(&spec, "completed", None, json!({})).unwrap();
        panic!("configured fault point was not reached");
    });
}

#[test]
fn commits_survive_crash_at_each_publication_boundary() {
    for prepared in [false, true] {
        for point in [
            "after_part_rename",
            "after_part_commit",
            "before_final_commit",
            "after_final_commit",
        ] {
            let temp = tempfile::tempdir().unwrap();
            let mut cmd = std::process::Command::new(std::env::current_exe().unwrap());
            cmd.args(["--exact", "publication_tests::fault_child", "--nocapture"])
                .env("ROWTRAIL_TEST_COMMIT_WORKSPACE", temp.path())
                .env("ROWTRAIL_TEST_COMMIT_FAULT", point);
            if prepared {
                cmd.env("ROWTRAIL_TEST_PREPARE", "1");
            }
            let output = cmd.output().unwrap();
            assert_eq!(
                output.status.code(),
                Some(86),
                "{point}: {}",
                String::from_utf8_lossy(&output.stderr)
            );
            let db = Db::open(temp.path()).unwrap();
            let (job, result): (String, String) = db
                .conn
                .lock()
                .unwrap()
                .query_row("SELECT id,result_ref FROM jobs", [], |r| {
                    Ok((r.get(0)?, r.get(1)?))
                })
                .unwrap();
            let completed = point == "after_final_commit";
            assert_eq!(
                db.job(&job).unwrap()["state"],
                if completed {
                    "completed"
                } else {
                    "interrupted"
                }
            );
            if prepared {
                assert_eq!(db.object("mf_test").is_ok(), completed, "{point}");
            } else if point == "after_part_rename" {
                assert!(crate::results::snapshot(&db, &result, None).is_err());
                assert_eq!(
                    std::fs::read_dir(temp.path().join("store").join(result))
                        .unwrap()
                        .count(),
                    0
                );
            } else {
                let snapshot = crate::results::snapshot(&db, &result, None).unwrap();
                assert_eq!(snapshot.rows, 1);
                assert_eq!(snapshot.quality["final_for_request"], completed);
                let rows = crate::results::read(
                    &db,
                    &rowtrail_contracts::ReadParams {
                        result_ref: result,
                        revision: Some(snapshot.revision),
                        cursor: None,
                        columns: vec![],
                        max_rows: 10,
                        max_bytes: 8192,
                    },
                )
                .unwrap();
                assert_eq!(rows["rows"], json!([["7"]]));
            }
        }
    }
}
