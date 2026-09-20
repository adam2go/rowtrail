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
    /// Open one checked connection for a sequence of agent operations.
    pub async fn session(&self) -> Result<Session> {
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
        Ok(Session {
            stream: Some(stream),
        })
    }
    pub async fn call(&self, request: &Request) -> Result<Response> {
        self.session().await?.call(request).await
    }
}

/// Sequential, bounded RPCs without reconnecting or handshaking per operation.
/// A failed or cancelled transport call consumes the connection: never silently
/// replay a mutation whose response may have been lost. Open a new session and
/// use the original idempotency key if the caller chooses to retry.
pub struct Session {
    stream: Option<UnixStream>,
}
impl Session {
    pub async fn call(&mut self, request: &Request) -> Result<Response> {
        let request = normalize(request)?;
        let mut stream = self
            .stream
            .take()
            .context("session closed; open a new session")?;
        let response = tokio::time::timeout(Duration::from_secs(65), async {
            send(&mut stream, &request).await?;
            receive::<Response>(&mut stream).await
        })
        .await??;
        ensure!(
            response.api_version == API_VERSION,
            "PROTOCOL_VERSION_MISMATCH"
        );
        self.stream = Some(stream);
        Ok(response)
    }
}
fn normalize(request: &Request) -> Result<Request> {
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
    Ok(request)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn cancelling_rpc_closes_session_without_replay() {
        let (client, mut server) = UnixStream::pair().unwrap();
        let mut session = Session {
            stream: Some(client),
        };
        let (seen_tx, seen_rx) = tokio::sync::oneshot::channel();
        let peer = tokio::spawn(async move {
            let _: Request = receive(&mut server).await.unwrap();
            seen_tx.send(()).unwrap();
            assert!(receive::<Request>(&mut server).await.is_err());
        });
        let request = Request::new("query", serde_json::json!({}));
        {
            let call = session.call(&request);
            tokio::pin!(call);
            tokio::select! {
                result = &mut call => panic!("unexpected response: {result:?}"),
                _ = seen_rx => {},
            }
        }
        assert!(session.stream.is_none());
        assert!(
            session
                .call(&request)
                .await
                .unwrap_err()
                .to_string()
                .contains("session closed")
        );
        peer.await.unwrap();
    }

    #[tokio::test]
    async fn session_reuses_connection_for_complete_responses() {
        let (client, mut server) = UnixStream::pair().unwrap();
        let mut session = Session {
            stream: Some(client),
        };
        let peer = tokio::spawn(async move {
            for _ in 0..2 {
                let request: Request = receive(&mut server).await.unwrap();
                send(
                    &mut server,
                    &Response::success(&request, serde_json::json!({})),
                )
                .await
                .unwrap();
            }
        });
        for _ in 0..2 {
            let request = Request::new("doctor", serde_json::json!({}));
            assert_eq!(
                session.call(&request).await.unwrap().request_id,
                request.request_id
            );
        }
        peer.await.unwrap();
    }
}
