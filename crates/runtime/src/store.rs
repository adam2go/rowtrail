//! A read-only, manifest-restricted local ObjectStore with byte reservations.
use crate::sources::SourceFile;
use futures::{StreamExt, TryStreamExt, stream::BoxStream};
use object_store::{path::Path, *};
use std::{
    collections::BTreeMap,
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
        serde_json::json!({"reserved_read_bytes":self.reserved.load(Ordering::Relaxed),"source_read_bytes":self.source_bytes.load(Ordering::Relaxed),"result_read_bytes":self.result_bytes.load(Ordering::Relaxed),"read_requests":self.requests.load(Ordering::Relaxed)})
    }
}
#[derive(Debug)]
pub struct CountStore {
    inner: object_store::local::LocalFileSystem,
    allowed: BTreeMap<Path, (SourceFile, bool)>,
    pub counters: Arc<Counters>,
    limit: u64,
    // One verified part, at most 8 MiB, shared by footer/body range requests.
    // Return slices of these exact bytes: never verify one read and parse another.
    verified: tokio::sync::Mutex<Option<(Path, bytes::Bytes)>>,
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
            let path = Path::from_filesystem_path(&f.path)?;
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
            verified: tokio::sync::Mutex::new(None),
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
    async fn get_opts(&self, path: &Path, opts: GetOptions) -> Result<GetResult> {
        let (identity, source) = self
            .allowed
            .get(path)
            .ok_or_else(|| err("UNREGISTERED_SOURCE"))?;
        identity
            .validate()
            .map_err(|e| self.counters.record_failure(e))?;
        let head = opts.head;
        let result = self.inner.get_opts(path, opts).await?;
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
            if cached.as_ref().is_none_or(|(key, _)| key != path) {
                self.counters
                    .reserved
                    .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |x| {
                        x.checked_add(identity.size).filter(|v| *v <= self.limit)
                    })
                    .map_err(|_| self.counters.exhausted())?;
                let identity = identity.clone();
                let checksum = checksum.clone();
                let bytes = tokio::task::spawn_blocking(move || {
                    crate::results::verified_bytes(&identity.path, identity.size, &checksum)
                })
                .await
                .map_err(err)?
                .map_err(|e| self.counters.record_failure(e))?;
                self.counters
                    .result_bytes
                    .fetch_add(bytes.len() as u64, Ordering::Relaxed);
                *cached = Some((path.clone(), bytes.into()));
            }
            let bytes = cached
                .as_ref()
                .unwrap()
                .1
                .slice(range.start as usize..range.end as usize);
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
