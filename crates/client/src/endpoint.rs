//! Short stable socket address plus a private, versioned workspace descriptor.
use anyhow::{Result, ensure};
use rowtrail_contracts::{API_VERSION, ApiError};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{
    io::{Read, Write},
    os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf},
};

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Endpoint {
    pub descriptor_version: u32,
    pub api_version: String,
    pub runtime_version: String,
    pub workspace: PathBuf,
    pub socket: PathBuf,
    pub store_id: String,
    pub pid: u32,
    pub uid: u32,
}
pub fn path(workspace: &Path) -> PathBuf {
    workspace.join("runtime-endpoint.json")
}
pub fn failure(workspace: &Path, code: &str, message: impl ToString, retryable: bool) -> ApiError {
    let mut e = ApiError::new(code, message);
    e.retryable = retryable;
    e.details = json!({"workspace":workspace,"endpoint":path(workspace),"runtime_log":workspace.join("logs/runtime.log"),
        "recovery":"Reconnect to inspect durable state. If an older coordinator owns this workspace, close its sessions and let it exit before upgrading. Never automatically replay a possibly accepted request."});
    e
}
pub fn load(workspace: &Path) -> Result<Option<Endpoint>> {
    let file = match std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path(workspace))
    {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(failure(workspace, "ENDPOINT_INVALID", e, false).into()),
    };
    let result = (|| -> Result<Endpoint> {
        let meta = file.metadata()?;
        ensure!(
            meta.is_file()
                && meta.uid() == unsafe { libc::geteuid() }
                && meta.permissions().mode() & 0o077 == 0
                && meta.len() <= 16384,
            "insecure or oversized endpoint descriptor"
        );
        let mut bytes = Vec::new();
        file.take(16385).read_to_end(&mut bytes)?;
        ensure!(bytes.len() <= 16384, "oversized endpoint descriptor");
        let endpoint: Endpoint = serde_json::from_slice(&bytes)?;
        ensure!(
            endpoint.descriptor_version == 1
                && endpoint.api_version == API_VERSION
                && endpoint.workspace == workspace
                && endpoint.uid == unsafe { libc::geteuid() }
                && endpoint.socket == super::socket_path(workspace),
            "endpoint identity mismatch"
        );
        Ok(endpoint)
    })();
    result
        .map(Some)
        .map_err(|e| failure(workspace, "ENDPOINT_INVALID", e, false).into())
}
pub fn publish(endpoint: &Endpoint) -> Result<()> {
    let temporary = endpoint
        .workspace
        .join(format!(".endpoint-{}.tmp", rowtrail_contracts::id("write")));
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&temporary)?;
    let result = (|| -> Result<()> {
        file.write_all(&serde_json::to_vec(endpoint)?)?;
        // Discovery is ephemeral: after OS failure no coordinator is alive.
        // Complete write + atomic rename suffices for live readers. Data/jobs
        // retain their separate durability barriers; do not flush them here.
        std::fs::rename(&temporary, path(&endpoint.workspace))?;

        Ok(())
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(temporary);
    }
    result
}
/// Retain typed errors across CLI, NDJSON and MCP; never parse arbitrary text.
pub fn api_error(error: &anyhow::Error, fallback: &str) -> ApiError {
    error
        .chain()
        .find_map(|e| e.downcast_ref::<ApiError>().cloned())
        .unwrap_or_else(|| ApiError::new(fallback, format!("{error:#}")))
}
