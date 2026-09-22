//! A read-only, manifest-restricted local ObjectStore with byte reservations.
use crate::sources::SourceFile;
use futures::{StreamExt, TryStreamExt, stream::BoxStream};
use object_store::{path::Path, *};
use std::{
    collections::{BTreeMap, VecDeque},
    fmt,
    io::{Read, Seek, SeekFrom},
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
};

#[derive(Debug, Default)]
pub struct Counters {
    pub reserved: AtomicU64,
    pub source_bytes: AtomicU64,
    pub result_bytes: AtomicU64,
    pub requests: AtomicU64,
    cache_hits: AtomicU64,
    cache_misses: AtomicU64,
    cache_peak: AtomicU64,
    verified_load_ns: AtomicU64,
    failure: std::sync::Mutex<Option<&'static str>>,
}
impl Counters {
    pub fn failure_code(&self) -> Option<&'static str> {
        *self.failure.lock().unwrap()
    }
    fn record_failure(&self, error: anyhow::Error) -> Error {
        let code = crate::errors::code(&error, "IO_ERROR");
        *self.failure.lock().unwrap() = Some(code);
        err(error)
    }
    fn exhausted(&self) -> Error {
        self.record_failure(anyhow::anyhow!("RESOURCE_EXHAUSTED: scan byte budget"))
    }

    pub fn value(&self) -> serde_json::Value {
        serde_json::json!({"reserved_read_bytes":self.reserved.load(Ordering::Relaxed),"source_read_bytes":self.source_bytes.load(Ordering::Relaxed),"result_read_bytes":self.result_bytes.load(Ordering::Relaxed),"read_requests":self.requests.load(Ordering::Relaxed),"verified_cache_hits":self.cache_hits.load(Ordering::Relaxed),"verified_cache_misses":self.cache_misses.load(Ordering::Relaxed),"verified_cache_peak_bytes":self.cache_peak.load(Ordering::Relaxed),"verified_part_load_ms":self.verified_load_ns.load(Ordering::Relaxed) as f64 / 1_000_000.0})
    }
}
#[derive(Debug)]
pub struct CountStore {
    inner: object_store::local::LocalFileSystem,
    allowed: BTreeMap<Path, (SourceFile, bool)>,
    pub counters: Arc<Counters>,
    limit: u64,
    // Job-local cache of exactly the bytes verified, never a cross-job shortcut.
    // Several compressed parts can share the existing 8 MiB cache budget.
    verified: tokio::sync::Mutex<VerifiedCache>,
}

const VERIFIED_CACHE_BYTES: usize = 8 * 1024 * 1024;
#[derive(Debug, Default)]
struct VerifiedCache {
    entries: VecDeque<(Path, bytes::Bytes)>,
    bytes: usize,
}
impl VerifiedCache {
    fn get(&mut self, path: &Path) -> Option<bytes::Bytes> {
        let index = self.entries.iter().position(|(key, _)| key == path)?;
        let entry = self.entries.remove(index).unwrap();
        let bytes = entry.1.clone();
        self.entries.push_back(entry);
        Some(bytes)
    }
    fn insert(&mut self, path: Path, bytes: bytes::Bytes) {
        while self.bytes + bytes.len() > VERIFIED_CACHE_BYTES || self.entries.len() >= 128 {
            let Some((_, removed)) = self.entries.pop_front() else {
                return;
            };
            self.bytes -= removed.len();
        }
        self.bytes += bytes.len();
        self.entries.push_back((path, bytes));
    }
}
fn err(s: impl ToString) -> Error {
    Error::Generic {
        store: "rowtrail",
        source: Box::new(std::io::Error::other(s.to_string())),
    }
}
impl CountStore {
    pub fn new(
        files: impl Iterator<Item = (SourceFile, bool)>,
        limit: u64,
        counters: Arc<Counters>,
    ) -> anyhow::Result<Self> {
        let mut allowed: BTreeMap<Path, (SourceFile, bool)> = BTreeMap::new();
        for (f, source) in files {
            let path = if f.inline.is_some() {
                Path::from_absolute_path(&f.path)?
            } else {
                Path::from_filesystem_path(&f.path)?
            };
            if let Some((previous, external)) = allowed.get_mut(&path) {
                // A second external alias must not downgrade a managed file's
                // checksum requirement. Account a shared managed read once.
                if let (Some(a), Some(b)) = (&previous.checksum, &f.checksum) {
                    anyhow::ensure!(a == b, "RESULT_CORRUPT: conflicting input digests");
                }
                if previous.checksum.is_none() {
                    previous.checksum = f.checksum;
                }
                *external &= source;
            } else {
                allowed.insert(path, (f, source));
            }
        }
        Ok(Self {
            inner: object_store::local::LocalFileSystem::new(),
            allowed,
            counters,
            limit,
            verified: tokio::sync::Mutex::new(VerifiedCache::default()),
        })
    }
}
impl fmt::Display for CountStore {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "RowTrailManifestStore")
    }
}
#[async_trait::async_trait]
impl ObjectStore for CountStore {
    async fn get_ranges(
        &self,
        path: &Path,
        ranges: &[std::ops::Range<u64>],
    ) -> Result<Vec<bytes::Bytes>> {
        let (identity, source) = self
            .allowed
            .get(path)
            .ok_or_else(|| err("UNREGISTERED_SOURCE"))?;
        let total = ranges.iter().try_fold(0u64, |sum, range| {
            if range.start > range.end || range.end > identity.size {
                return Err(err("invalid read range"));
            }
            sum.checked_add(range.end - range.start)
                .ok_or_else(|| err("read range overflow"))
        })?;
        // Parquet requests several column ranges together. Read a bounded batch
        // in one blocking task/file open, without fetching gaps between columns.
        // Managed parts retain the same verify-once-per-part byte path below.
        if identity.checksum.is_some() || total > 8 * 1024 * 1024 {
            return coalesce_ranges(
                ranges,
                |range| self.get_range(path, range),
                OBJECT_STORE_COALESCE_DEFAULT,
            )
            .await;
        }
        identity
            .validate()
            .map_err(|e| self.counters.record_failure(e))?;
        self.counters
            .reserved
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |n| {
                n.checked_add(total).filter(|n| *n <= self.limit)
            })
            .map_err(|_| self.counters.exhausted())?;
        if ranges.is_empty() {
            return Ok(vec![]);
        }
        let identity = identity.clone();
        let source = *source;
        let counters = self.counters.clone();
        let ranges = ranges.to_vec();
        tokio::task::spawn_blocking(move || {
            let mut file = std::fs::File::open(&identity.path).map_err(err)?;
            let mut output = Vec::with_capacity(ranges.len());
            for range in ranges {
                file.seek(SeekFrom::Start(range.start)).map_err(err)?;
                counters.requests.fetch_add(1, Ordering::Relaxed);
                let mut bytes = vec![0; (range.end - range.start) as usize];
                let mut read = 0;
                while read < bytes.len() {
                    let n = file.read(&mut bytes[read..]).map_err(err)?;
                    if n == 0 {
                        return Err(err("source ended during range read"));
                    }
                    if source {
                        counters.source_bytes.fetch_add(n as u64, Ordering::Relaxed);
                    } else {
                        counters.result_bytes.fetch_add(n as u64, Ordering::Relaxed);
                    }
                    read += n;
                }
                output.push(bytes.into());
            }
            identity
                .validate()
                .map_err(|e| counters.record_failure(e))?;
            Ok(output)
        })
        .await
        .map_err(err)?
    }
    async fn get_opts(&self, path: &Path, opts: GetOptions) -> Result<GetResult> {
        let (identity, source) = self
            .allowed
            .get(path)
            .ok_or_else(|| err("UNREGISTERED_SOURCE"))?;
        identity
            .validate()
            .map_err(|e| self.counters.record_failure(e))?;
        let head = opts.head;
        let result = if identity.inline.is_some() {
            let meta = ObjectMeta {
                location: path.clone(),
                size: identity.size,
                last_modified: std::time::UNIX_EPOCH.into(),
                e_tag: identity.checksum.clone(),
                version: None,
            };
            opts.check_preconditions(&meta)?;
            let range = opts
                .range
                .as_ref()
                .map(|r| r.as_range(identity.size))
                .transpose()
                .map_err(err)?
                .unwrap_or(0..identity.size);
            GetResult {
                payload: GetResultPayload::Stream(futures::stream::empty().boxed()),
                meta,
                range,
                attributes: Attributes::default(),
            }
        } else {
            self.inner.get_opts(path, opts).await?
        };
        if head {
            return Ok(result);
        }
        self.counters.requests.fetch_add(1, Ordering::Relaxed);
        let GetResult {
            payload,
            meta,
            range,
            attributes,
        } = result;
        if let Some(checksum) = &identity.checksum {
            let mut cached = self.verified.lock().await;
            let bytes = if let Some(bytes) = cached.get(path) {
                self.counters.cache_hits.fetch_add(1, Ordering::Relaxed);
                bytes
            } else {
                self.counters.cache_misses.fetch_add(1, Ordering::Relaxed);
                self.counters
                    .reserved
                    .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |x| {
                        x.checked_add(identity.size).filter(|v| *v <= self.limit)
                    })
                    .map_err(|_| self.counters.exhausted())?;
                let identity = identity.clone();
                let checksum = checksum.clone();
                let loaded_at = std::time::Instant::now();
                let bytes = tokio::task::spawn_blocking(move || {
                    crate::results::verified_part(&identity, &checksum)
                })
                .await
                .map_err(err)?
                .map_err(|e| self.counters.record_failure(e))?;
                self.counters.verified_load_ns.fetch_add(
                    loaded_at.elapsed().as_nanos().min(u64::MAX as u128) as u64,
                    Ordering::Relaxed,
                );
                self.counters
                    .result_bytes
                    .fetch_add(bytes.len() as u64, Ordering::Relaxed);
                let bytes: bytes::Bytes = bytes.into();
                cached.insert(path.clone(), bytes.clone());
                self.counters
                    .cache_peak
                    .fetch_max(cached.bytes as u64, Ordering::Relaxed);
                bytes
            };
            let bytes = bytes.slice(range.start as usize..range.end as usize);
            return Ok(GetResult {
                payload: GetResultPayload::Stream(
                    futures::stream::once(async move { Ok(bytes) }).boxed(),
                ),
                meta,
                range,
                attributes,
            });
        }
        let GetResultPayload::File(mut file, _) = payload else {
            return Err(err("unexpected non-local payload"));
        };
        file.seek(SeekFrom::Start(range.start)).map_err(err)?;
        let count = self.counters.clone();
        let limit = self.limit;
        let source = *source;
        let payload = futures::stream::try_unfold(
            (file, range.end - range.start),
            move |(mut file, left)| {
                let count = count.clone();
                async move {
                    if left == 0 {
                        return Ok(None);
                    }
                    let n = left.min(64 * 1024);
                    count
                        .reserved
                        .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |x| {
                            x.checked_add(n).filter(|v| *v <= limit)
                        })
                        .map_err(|_| count.exhausted())?;
                    let (file, bytes) = tokio::task::spawn_blocking(move || {
                        let mut b = vec![0; n as usize];
                        file.read_exact(&mut b).map_err(err)?;
                        Ok::<_, Error>((file, bytes::Bytes::from(b)))
                    })
                    .await
                    .map_err(err)??;
                    if source {
                        count.source_bytes.fetch_add(n, Ordering::Relaxed);
                    } else {
                        count.result_bytes.fetch_add(n, Ordering::Relaxed);
                    }
                    Ok(Some((bytes, (file, left - n))))
                }
            },
        )
        .boxed();
        Ok(GetResult {
            payload: GetResultPayload::Stream(payload),
            meta,
            range,
            attributes,
        })
    }
    fn list(&self, _: Option<&Path>) -> BoxStream<'static, Result<ObjectMeta>> {
        futures::stream::once(async {
            Err(err(
                "UNSUPPORTED_OPERATION: query providers must use frozen file paths",
            ))
        })
        .boxed()
    }
    async fn list_with_delimiter(&self, _: Option<&Path>) -> Result<ListResult> {
        Err(err("UNSUPPORTED_OPERATION"))
    }
    async fn put_opts(&self, _: &Path, _: PutPayload, _: PutOptions) -> Result<PutResult> {
        Err(err("READ_ONLY"))
    }
    async fn put_multipart_opts(
        &self,
        _: &Path,
        _: PutMultipartOptions,
    ) -> Result<Box<dyn MultipartUpload>> {
        Err(err("READ_ONLY"))
    }
    fn delete_stream(
        &self,
        locations: BoxStream<'static, Result<Path>>,
    ) -> BoxStream<'static, Result<Path>> {
        locations
            .and_then(|_| async { Err(err("READ_ONLY")) })
            .boxed()
    }
    async fn copy_opts(&self, _: &Path, _: &Path, _: CopyOptions) -> Result<()> {
        Err(err("READ_ONLY"))
    }
}
