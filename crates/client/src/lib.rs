//! Lightweight local client. This crate deliberately has no engine dependency.
use anyhow::{Context, Result, ensure};
use rowtrail_contracts::{API_VERSION, FRAME_LIMIT, Request, Response};
use sha2::{Digest, Sha256};
use std::{
    os::unix::{fs::PermissionsExt, process::CommandExt},
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::UnixStream,
};

pub fn workspace_path(path: &Path) -> Result<PathBuf> {
    if !path.exists() {
        std::fs::create_dir_all(path)?;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))?;
    }
    let path = path.canonicalize()?;
    ensure!(
        std::fs::metadata(&path)?.permissions().mode() & 0o077 == 0,
        "workspace must be accessible only by its owner (chmod 700)"
    );
    Ok(path)
}
pub fn socket_path(workspace: &Path) -> PathBuf {
    // A private short directory avoids Unix sockaddr path length limits.
    let key = hex::encode(Sha256::digest(workspace.as_os_str().as_encoded_bytes()));
    let uid = unsafe { libc::geteuid() };
    std::env::temp_dir()
        .join(format!("rowtrail-{uid}-{}", &key[..20]))
        .join("runtime.sock")
}
pub async fn send(stream: &mut UnixStream, value: &impl serde::Serialize) -> Result<()> {
    let bytes = serde_json::to_vec(value)?;
    ensure!(bytes.len() <= FRAME_LIMIT, "PROTOCOL_FRAME_TOO_LARGE");
    stream.write_u32(bytes.len() as u32).await?;
    stream.write_all(&bytes).await?;
    Ok(())
}
pub async fn receive<T: serde::de::DeserializeOwned>(stream: &mut UnixStream) -> Result<T> {
    let size = stream.read_u32().await? as usize;
    ensure!(size > 0 && size <= FRAME_LIMIT, "PROTOCOL_FRAME_TOO_LARGE");
    let mut bytes = vec![0; size];
    stream.read_exact(&mut bytes).await?;
    Ok(serde_json::from_slice(&bytes)?)
}
#[derive(Clone)]
pub struct Client {
    pub workspace: PathBuf,
}
impl Client {
    pub fn new(path: impl AsRef<Path>) -> Result<Self> {
        Ok(Self {
            workspace: workspace_path(path.as_ref())?,
        })
    }
    async fn connect(&self) -> Result<UnixStream> {
        let socket = socket_path(&self.workspace);
        if let Ok(s) = UnixStream::connect(&socket).await {
            return Ok(s);
        }
        let runtime = std::env::var_os("ROWTRAIL_RUNTIME")
            .map(PathBuf::from)
            .unwrap_or(std::env::current_exe()?.with_file_name("rowtrail-runtime"));
        let logs = self.workspace.join("logs");
        std::fs::create_dir_all(&logs)?;
        let log = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(logs.join("runtime.log"))?;
        let mut command = std::process::Command::new(&runtime);
        command
            .arg("serve")
            .arg("--workspace")
            .arg(&self.workspace)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(log);
        unsafe {
            command.pre_exec(|| {
                if libc::setsid() == -1 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let mut child = command
            .spawn()
            .with_context(|| format!("cannot start {}", runtime.display()))?;
        for _ in 0..500 {
            tokio::time::sleep(Duration::from_millis(10)).await;
            if let Ok(s) = UnixStream::connect(&socket).await {
                // A short-lived competing coordinator may exit; reap only ours.
                std::thread::spawn(move || {
                    let _ = child.wait();
                });
                return Ok(s);
            }
            if let Some(status) = child.try_wait()?
                && !status.success()
            {
                anyhow::bail!(
                    "runtime startup failed; inspect {}",
                    logs.join("runtime.log").display()
                )
            }
        }
        anyhow::bail!(
            "runtime startup timed out; inspect {}",
            logs.join("runtime.log").display()
        )
    }
    pub async fn call(&self, request: &Request) -> Result<Response> {
        // A shared coordinator's cwd is unrelated to later callers' working directories.
        let mut request = request.clone();
        let field = match request.method.as_str() {
            "open" => Some("source"),
            "export" => Some("destination"),
            _ => None,
        };
        if let Some(field) = field
            && let Some(path) = request
                .params
                .get(field)
                .and_then(serde_json::Value::as_str)
        {
            request.params[field] = serde_json::to_value(std::path::absolute(path)?)?;
        }
        let mut stream = self.connect().await?;
        ensure!(
            stream.peer_cred()?.uid() == unsafe { libc::geteuid() },
            "unauthorized local runtime"
        );
        send(
            &mut stream,
            &Request::new("handshake", serde_json::json!({})),
        )
        .await?;
        let hello: Response = receive(&mut stream).await?;
        ensure!(
            hello.ok && hello.api_version == API_VERSION,
            "PROTOCOL_VERSION_MISMATCH"
        );
        send(&mut stream, &request).await?;
        tokio::time::timeout(Duration::from_secs(65), receive(&mut stream)).await?
    }
}
