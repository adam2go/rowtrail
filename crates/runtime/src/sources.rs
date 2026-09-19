use anyhow::{Result, bail, ensure};
use arrow::datatypes::{DataType, Field, Schema};
use parquet::{
    arrow::arrow_reader::ParquetRecordBatchReaderBuilder,
    file::reader::{ChunkReader, Length},
};
use rowtrail_contracts::{OpenParams, id};
use serde::{Deserialize, Serialize};
use std::{
    fs::File,
    io::{Read, Seek, SeekFrom},
    os::unix::fs::MetadataExt,
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceFile {
    pub path: PathBuf,
    pub size: u64,
    pub device: u64,
    pub inode: u64,
    pub mtime_sec: i64,
    pub mtime_nsec: i64,
    pub rows: Option<u64>,
    pub row_groups: Option<usize>,
}
impl SourceFile {
    pub fn inspect(path: &Path) -> Result<Self> {
        let m = std::fs::metadata(path)?;
        ensure!(
            m.is_file(),
            "INVALID_ARGUMENT: source must be a regular file"
        );
        Ok(Self {
            path: path.canonicalize()?,
            size: m.len(),
            device: m.dev(),
            inode: m.ino(),
            mtime_sec: m.mtime(),
            mtime_nsec: m.mtime_nsec(),
            rows: None,
            row_groups: None,
        })
    }
    pub fn validate(&self) -> Result<()> {
        let now = Self::inspect(&self.path)
            .map_err(|e| anyhow::anyhow!("SOURCE_CHANGED: {}: {e}", self.path.display()))?;
        ensure!(
            (
                now.size,
                now.device,
                now.inode,
                now.mtime_sec,
                now.mtime_nsec
            ) == (
                self.size,
                self.device,
                self.inode,
                self.mtime_sec,
                self.mtime_nsec
            ),
            "SOURCE_CHANGED: {}",
            self.path.display()
        );
        Ok(())
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Manifest {
    pub id: String,
    pub dataset_ref: String,
    pub source: PathBuf,
    pub format: String,
    pub schema: Schema,
    pub files: Vec<SourceFile>,
    pub header: bool,
    pub delimiter: u8,
    pub schema_origin: String,
    pub metadata_bytes: u64,
    pub inferred_rows: usize,
}
impl Manifest {
    pub fn validate(&self) -> Result<()> {
        for f in &self.files {
            f.validate()?;
        }
        Ok(())
    }
}

struct CountReader {
    inner: File,
    count: Arc<AtomicU64>,
    remaining: u64,
}
impl Read for CountReader {
    fn read(&mut self, b: &mut [u8]) -> std::io::Result<usize> {
        if self.remaining == 0 {
            return Err(std::io::Error::other(
                "RESOURCE_EXHAUSTED: metadata byte budget",
            ));
        }
        let len = b.len().min(self.remaining as usize);
        let n = self.inner.read(&mut b[..len])?;
        self.remaining -= n as u64;
        self.count.fetch_add(n as u64, Ordering::Relaxed);
        Ok(n)
    }
}
struct CountFile {
    file: File,
    size: u64,
    count: Arc<AtomicU64>,
}
impl Length for CountFile {
    fn len(&self) -> u64 {
        self.size
    }
}
impl ChunkReader for CountFile {
    type T = CountReader;
    fn get_read(&self, start: u64) -> parquet::errors::Result<Self::T> {
        let mut f = self.file.try_clone()?;
        f.seek(SeekFrom::Start(start))?;
        Ok(CountReader {
            inner: f,
            count: self.count.clone(),
            remaining: self.size.saturating_sub(start) + 1,
        })
    }
    fn get_bytes(&self, start: u64, length: usize) -> parquet::errors::Result<bytes::Bytes> {
        if length > 8 * 1024 * 1024
            || self
                .count
                .load(Ordering::Relaxed)
                .saturating_add(length as u64)
                > 16 * 1024 * 1024
        {
            return Err(parquet::errors::ParquetError::General(
                "RESOURCE_EXHAUSTED: footer metadata budget".into(),
            ));
        }
        let mut bytes = vec![0; length];
        self.get_read(start)?.read_exact(&mut bytes)?;
        Ok(bytes.into())
    }
}
pub fn parse_type(value: &str) -> Result<DataType> {
    value
        .parse()
        .map_err(|e| anyhow::anyhow!("INVALID_ARGUMENT: invalid Arrow type {value}: {e}"))
}

pub fn open(p: &OpenParams) -> Result<Manifest> {
    ensure!(
        Path::new(&p.source).is_absolute(),
        "INVALID_ARGUMENT: protocol source paths must be absolute"
    );
    ensure!(
        p.infer_rows > 0 && p.infer_rows <= 10000,
        "INVALID_ARGUMENT: infer_rows must be 1..10000"
    );
    let source = Path::new(&p.source).canonicalize()?;
    let format = if p.format == "auto" {
        source
            .extension()
            .and_then(|x| x.to_str())
            .unwrap_or("parquet")
            .to_lowercase()
    } else {
        p.format.clone()
    };
    ensure!(
        matches!(format.as_str(), "parquet" | "csv" | "tsv"),
        "UNSUPPORTED_OPERATION: source format {format}"
    );
    let delimiter = match &p.delimiter {
        Some(s) => {
            ensure!(
                s.len() == 1 && s.is_ascii(),
                "INVALID_ARGUMENT: delimiter must be one ASCII byte"
            );
            s.as_bytes()[0]
        }
        None => {
            if format == "tsv" {
                b'\t'
            } else {
                b','
            }
        }
    };
    let mut paths = vec![];
    if source.is_file() {
        paths.push(source.clone())
    } else {
        let mut pending = vec![source.clone()];
        let mut visited = 0;
        while let Some(dir) = pending.pop() {
            for entry in std::fs::read_dir(dir)? {
                visited += 1;
                ensure!(
                    visited <= 4096,
                    "SOURCE_DISCOVERY_LIMIT: directory discovery exceeds current 4096-entry bound"
                );
                let entry = entry?;
                let ty = entry.file_type()?;
                if ty.is_dir() {
                    pending.push(entry.path())
                } else if ty.is_file()
                    && entry.path().extension().and_then(|x| x.to_str()) == Some(format.as_str())
                {
                    paths.push(entry.path());
                    ensure!(
                        paths.len() <= 128,
                        "SOURCE_DISCOVERY_LIMIT: current manifest supports at most 128 files"
                    );
                }
            }
        }
    }
    paths.sort();
    ensure!(!paths.is_empty(), "INVALID_ARGUMENT: no matching files");
    let count = Arc::new(AtomicU64::new(0));
    let provided = p
        .schema
        .as_ref()
        .map(|cols| {
            cols.iter()
                .map(|c| Ok(Field::new(&c.name, parse_type(&c.r#type)?, c.nullable)))
                .collect::<Result<Vec<_>>>()
                .map(Schema::new)
        })
        .transpose()?;
    let mut schema = provided.clone();
    let mut files = vec![];
    let mut inferred_rows = 0;
    let mut imported_partial = false;
    let mut imported_estimate = false;
    for path in paths {
        let mut info = SourceFile::inspect(&path)?;
        let current = if format == "parquet" {
            // Footer-only metadata access; no RecordBatch reader is built here.
            let reader = ParquetRecordBatchReaderBuilder::try_new(CountFile {
                file: File::open(&path)?,
                size: info.size,
                count: count.clone(),
            })?;
            info.rows = Some(reader.metadata().file_metadata().num_rows() as u64);
            info.row_groups = Some(reader.metadata().num_row_groups());
            reader.schema().as_ref().clone()
        } else if let Some(s) = &provided {
            s.clone()
        } else {
            let r = CountReader {
                inner: File::open(&path)?,
                count: count.clone(),
                remaining: 1024 * 1024,
            };
            let (s, n) = arrow::csv::reader::Format::default()
                .with_header(p.header)
                .with_delimiter(delimiter)
                .infer_schema(r, Some(p.infer_rows))?;
            inferred_rows += n;
            s
        };
        if let Some(raw) = current.metadata().get("rowtrail.quality") {
            let quality: serde_json::Value = serde_json::from_str(raw)?;
            imported_partial |= quality["coverage"]["kind"] != "complete";
            imported_estimate |= quality["accuracy"] != "exact";
        }
        if let Some(s) = &schema {
            ensure!(
                s.fields() == current.fields(),
                "SCHEMA_CONFLICT: {} differs from the frozen schema",
                path.display()
            )
        } else {
            schema = Some(current)
        }
        info.validate()?;
        files.push(info);
    }
    let mut schema = schema.unwrap();
    if imported_partial || imported_estimate {
        let mut metadata = schema.metadata().clone();
        metadata.insert(
            "rowtrail.quality".into(),
            serde_json::json!({
                "accuracy": if imported_estimate {"estimate"} else {"exact"},
                "coverage":{"kind":if imported_partial {"partial"} else {"complete"}}
            })
            .to_string(),
        );
        schema = schema.with_metadata(metadata);
    }
    let mut names = std::collections::HashSet::new();
    for f in schema.fields() {
        ensure!(
            names.insert(f.name()),
            "SCHEMA_CONFLICT: duplicate column name"
        );
    }
    if schema.fields().is_empty() {
        bail!("SCHEMA_CONFLICT: source has no fields")
    }
    Ok(Manifest {
        id: id("mf"),
        dataset_ref: id("ds"),
        source,
        format: format.clone(),
        schema,
        files,
        header: p.header,
        delimiter,
        schema_origin: if format == "parquet" {
            "metadata"
        } else if provided.is_some() {
            "provided"
        } else {
            "inferred"
        }
        .into(),
        metadata_bytes: count.load(Ordering::Relaxed),
        inferred_rows,
    })
}
