//! Browsing the shared folder.
//!
//! Everything here is reachable by any device that knows the PIN, so
//! `resolve` is the single most security-relevant function in the
//! project: it is what stops `../../` or a symlink from turning "browse
//! my Shared folder" into "browse my whole drive".

use std::path::{Component, Path, PathBuf};

use axum::body::Body;
use axum::extract::{Multipart, Query, State};
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::{Deserialize, Serialize};
use tokio::io::AsyncWriteExt;
use tokio_util::io::ReaderStream;

use crate::store::SharedState;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Entry {
    pub name: String,
    pub is_dir: bool,
    pub size: u64,
    /// Unix milliseconds, or `None` if the filesystem wouldn't say.
    pub modified: Option<u64>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Listing {
    /// Path relative to the shared root, `""` at the top.
    pub path: String,
    /// `None` when already at the top, so the interface knows not to
    /// offer a "go up" row that would escape the share.
    pub parent: Option<String>,
    pub entries: Vec<Entry>,
}

#[derive(Debug, Deserialize)]
pub struct PathQuery {
    #[serde(default)]
    pub path: String,
}

#[derive(Debug, Deserialize)]
pub struct RenameQuery {
    pub path: String,
    pub new_name: String,
}

// --------------------------------------------------------------- safety

#[derive(Debug)]
pub struct FileError(StatusCode, String);

impl IntoResponse for FileError {
    fn into_response(self) -> Response {
        (self.0, self.1).into_response()
    }
}

fn bad(msg: &str) -> FileError {
    FileError(StatusCode::BAD_REQUEST, msg.to_string())
}

fn missing(msg: &str) -> FileError {
    FileError(StatusCode::NOT_FOUND, msg.to_string())
}

fn failed(err: impl std::fmt::Display) -> FileError {
    FileError(StatusCode::INTERNAL_SERVER_ERROR, err.to_string())
}

/// Turns a client-supplied relative path into an absolute one that is
/// provably inside `root`.
///
/// Two independent checks, because either alone is insufficient:
///
///  1. Reject `..` and absolute/prefix components *before* touching the
///     filesystem. This catches traversal even for paths that don't
///     exist yet, which `canonicalize` cannot help with.
///  2. Canonicalize and confirm the result is still under the
///     canonicalized root. This is what catches symlinks — a link
///     inside the share pointing at `C:\` contains no `..` at all, so
///     check 1 would happily wave it through.
pub fn resolve(root: &Path, rel: &str) -> Result<PathBuf, FileError> {
    let rel = rel.trim_start_matches(['/', '\\']);
    let candidate = Path::new(rel);

    for component in candidate.components() {
        match component {
            Component::Normal(_) => {}
            Component::CurDir => {}
            Component::ParentDir => return Err(bad("Path may not contain '..'")),
            Component::RootDir | Component::Prefix(_) => {
                return Err(bad("Path must be relative to the shared folder"))
            }
        }
    }

    let joined = root.join(candidate);

    // Canonicalize the nearest existing ancestor: the target itself may
    // legitimately not exist yet (creating a folder, uploading a file).
    let (existing, trailing) = nearest_existing(&joined);
    let real_root = root.canonicalize().map_err(|_| {
        failed("The shared folder no longer exists. Pick a new one in Settings.")
    })?;
    let real = existing.canonicalize().map_err(failed)?;

    if !real.starts_with(&real_root) {
        return Err(bad("Path is outside the shared folder"));
    }

    // Only rejoin when there's actually a tail left. `Path::join("")`
    // appends a trailing separator, and on Windows `C:\dir\file.txt\` is
    // read as a directory path — `metadata()` fails on it, so `is_file()`
    // came back false for every file that existed. Directories tolerated
    // the trailing separator, which is why listing worked and download,
    // delete, and rename silently 404'd.
    if trailing.as_os_str().is_empty() {
        Ok(real)
    } else {
        Ok(real.join(trailing))
    }
}

/// Splits a path into (deepest existing ancestor, remaining tail).
fn nearest_existing(path: &Path) -> (PathBuf, PathBuf) {
    let mut existing = path.to_path_buf();
    let mut tail = PathBuf::new();

    while !existing.exists() {
        let Some(parent) = existing.parent().map(Path::to_path_buf) else {
            break;
        };
        let name = existing
            .file_name()
            .map(PathBuf::from)
            .unwrap_or_default();
        tail = if tail.as_os_str().is_empty() { name } else { name.join(&tail) };
        existing = parent;
    }

    (existing, tail)
}

/// A `Content-Disposition` value that survives a non-ASCII filename.
///
/// The quoted `filename=` form is ASCII by the grammar, so `café.txt` or
/// anything in CJK arrived mangled. RFC 6266 answers this with a second
/// `filename*` parameter carrying the real UTF-8 name percent-encoded;
/// browsers that understand it prefer it, and older ones fall back to
/// the ASCII approximation rather than getting nothing.
///
/// The fallback also drops quotes, backslashes and control characters —
/// the last of which would otherwise be an attempt to inject a header,
/// caught today only because the `http` crate refuses such values and
/// turns the download into a 500.
fn content_disposition(name: &str) -> String {
    let ascii: String = name
        .chars()
        .map(|c| {
            if c == '"' || c == '\\' || !(c.is_ascii_graphic() || c == ' ') {
                '_'
            } else {
                c
            }
        })
        .collect();

    let mut encoded = String::new();
    for byte in name.as_bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                encoded.push(*byte as char)
            }
            _ => encoded.push_str(&format!("%{byte:02X}")),
        }
    }

    format!("attachment; filename=\"{ascii}\"; filename*=UTF-8''{encoded}")
}

fn relative_to(root: &Path, path: &Path) -> String {
    root.canonicalize()
        .ok()
        .and_then(|r| path.strip_prefix(r).ok().map(Path::to_path_buf))
        .map(|p| p.to_string_lossy().replace('\\', "/"))
        .unwrap_or_default()
}

// ------------------------------------------------------------- handlers

pub async fn list(
    State(state): State<SharedState>,
    Query(q): Query<PathQuery>,
) -> Result<Json<Listing>, FileError> {
    let root = state.shared_root();
    let target = resolve(&root, &q.path)?;

    if !target.is_dir() {
        return Err(missing("That folder doesn't exist"));
    }

    let mut entries = Vec::new();
    let mut dir = tokio::fs::read_dir(&target).await.map_err(failed)?;

    while let Some(item) = dir.next_entry().await.map_err(failed)? {
        // A file that vanishes mid-listing shouldn't fail the whole
        // request — skip it and show the rest.
        let Ok(meta) = item.metadata().await else { continue };
        entries.push(Entry {
            name: item.file_name().to_string_lossy().to_string(),
            is_dir: meta.is_dir(),
            size: if meta.is_dir() { 0 } else { meta.len() },
            modified: meta
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_millis() as u64),
        });
    }

    // Folders first, then case-insensitive by name — the order people
    // expect from a file manager, not the arbitrary order the
    // filesystem hands back.
    entries.sort_by(|a, b| {
        b.is_dir
            .cmp(&a.is_dir)
            .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
    });

    let path = relative_to(&root, &target);
    let parent = if path.is_empty() {
        None
    } else {
        Some(match path.rsplit_once('/') {
            Some((head, _)) => head.to_string(),
            None => String::new(),
        })
    };

    Ok(Json(Listing { path, parent, entries }))
}

/// How much of a file to read at a time when serving it.
///
/// `ReaderStream` defaults to 4 KB. At gigabit that is roughly 28,000
/// reads a second, and each one costs a round trip to tokio's blocking
/// pool plus a progress update that takes a lock — pure overhead between
/// the disk and the socket. 256 KB cuts that by two orders of magnitude
/// for a quarter of a megabyte of buffer.
const READ_CHUNK: usize = 256 * 1024;

/// One byte range from a `Range` header, resolved against `len`.
///
/// `Ok(None)` means send the whole file; `Err(())` means the request
/// asked for something that isn't there and deserves a 416.
///
/// Only a single range is honoured. Multipart ranges are in the spec and
/// essentially nothing uses them for a plain file download — answering
/// one by sending the whole file is unhelpful but correct, whereas
/// half-implementing `multipart/byteranges` would not be.
fn parse_range(header: Option<&str>, len: u64) -> Result<Option<(u64, u64)>, ()> {
    let Some(raw) = header else { return Ok(None) };
    let Some(spec) = raw.trim().strip_prefix("bytes=") else {
        return Ok(None);
    };
    if spec.contains(',') {
        return Ok(None);
    }

    let (from, to) = spec.split_once('-').ok_or(())?;
    let (from, to) = (from.trim(), to.trim());

    let (start, end) = match (from.is_empty(), to.is_empty()) {
        // "bytes=-N" — the last N bytes.
        (true, false) => {
            let n: u64 = to.parse().map_err(|_| ())?;
            if n == 0 {
                return Err(());
            }
            (len.saturating_sub(n), len.saturating_sub(1))
        }
        // "bytes=N-" — from N to the end. This is the resume case.
        (false, true) => (from.parse().map_err(|_| ())?, len.saturating_sub(1)),
        // "bytes=N-M", clamped: asking past the end is not an error.
        (false, false) => {
            let start: u64 = from.parse().map_err(|_| ())?;
            let end: u64 = to.parse().map_err(|_| ())?;
            (start, end.min(len.saturating_sub(1)))
        }
        (true, true) => return Ok(None),
    };

    if len == 0 || start > end || start >= len {
        return Err(());
    }
    Ok(Some((start, end)))
}

pub async fn download(
    State(state): State<SharedState>,
    Query(q): Query<PathQuery>,
    headers: axum::http::HeaderMap,
    client: Option<axum::Extension<crate::server::ClientId>>,
) -> Result<Response, FileError> {
    let root = state.shared_root();
    let target = resolve(&root, &q.path)?;

    if !target.is_file() {
        return Err(missing("That file doesn't exist"));
    }

    let name = target
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| "download".into());
    let len = tokio::fs::metadata(&target).await.map_err(failed)?.len();
    let mime = mime_guess::from_path(&target)
        .first_or_octet_stream()
        .to_string();

    let recipient = client
        .map(|axum::Extension(crate::server::ClientId(id))| state.client_label(&id))
        .unwrap_or_else(|| "This PC".to_string());

    let range = match parse_range(
        headers.get(header::RANGE).and_then(|v| v.to_str().ok()),
        len,
    ) {
        Ok(range) => range,
        Err(()) => {
            return Ok((
                StatusCode::RANGE_NOT_SATISFIABLE,
                [(header::CONTENT_RANGE, format!("bytes */{len}"))],
            )
                .into_response())
        }
    };

    let (start, end) = range.unwrap_or((0, len.saturating_sub(1)));
    let span = if len == 0 { 0 } else { end - start + 1 };

    // A resumed or chunked fetch is the same download carrying on, not a
    // new one. Recording every range would turn one 200 MB transfer into
    // forty identical rows saying the same thing. The cost is that
    // resuming from the middle isn't recorded as its own event — which
    // it isn't.
    let transfer_id = if range.map_or(true, |(from, _)| from == 0) {
        let id = crate::model::random_id();
        // Unlike an upload, the size is known up front, so this progress
        // is a real percentage rather than a running total.
        state.add_transfer(crate::model::Transfer {
            id: id.clone(),
            name: name.clone(),
            device_id: None,
            device_name: recipient,
            direction: crate::model::TransferDirection::Download,
            state: crate::model::TransferState::Active,
            bytes_done: 0,
            bytes_total: span,
            rate: None,
            started_at: crate::server::now_iso(),
            finished_at: None,
            error: None,
        });
        Some(id)
    } else {
        None
    };

    // Streamed, not read into memory — a 4 GB video must not become 4 GB
    // of RAM on a machine that's also serving other requests.
    let mut file = tokio::fs::File::open(&target).await.map_err(failed)?;
    if start > 0 {
        use tokio::io::AsyncSeekExt;
        file.seek(std::io::SeekFrom::Start(start))
            .await
            .map_err(failed)?;
    }
    // `take` even for a whole-file send, so both paths produce the same
    // reader type — and so the body can never run past the length the
    // Content-Length header just promised.
    let reader = tokio::io::AsyncReadExt::take(file, span);

    let body = Body::from_stream(TrackedDownload {
        inner: ReaderStream::with_capacity(reader, READ_CHUNK),
        state: state.clone(),
        id: transfer_id,
        sent: 0,
        total: span,
        settled: false,
    });

    let mut response = (
        if range.is_some() {
            StatusCode::PARTIAL_CONTENT
        } else {
            StatusCode::OK
        },
        [
            (header::CONTENT_TYPE, mime),
            (header::CONTENT_LENGTH, span.to_string()),
            // Advertised even on a whole-file response: it is how a
            // client knows it may resume this download later rather than
            // starting a 20 GB file again from zero.
            (header::ACCEPT_RANGES, "bytes".to_string()),
            (header::CONTENT_DISPOSITION, content_disposition(&name)),
        ],
        body,
    )
        .into_response();

    if range.is_some() {
        if let Ok(value) =
            axum::http::HeaderValue::from_str(&format!("bytes {start}-{end}/{len}"))
        {
            response.headers_mut().insert(header::CONTENT_RANGE, value);
        }
    }

    Ok(response)
}

/// Bulk download: several files and folders as one zip.
///
/// Built on a worker thread and streamed out through a channel, so a
/// 20 GB selection costs a small buffer rather than 20 GB of memory. The
/// archive has no total size until it's finished, which is why this one
/// can't report a percentage.
pub async fn download_zip(
    State(state): State<SharedState>,
    axum::extract::RawQuery(raw): axum::extract::RawQuery,
    client: Option<axum::Extension<crate::server::ClientId>>,
) -> Result<Response, FileError> {
    let selected = crate::server::query_values(raw.as_deref(), "path");
    if selected.is_empty() {
        return Err(bad("Nothing selected to download"));
    }

    let root = state.shared_root();

    // Resolve everything up front: a traversal attempt should fail the
    // request outright, not halfway through a stream the client is
    // already saving to disk.
    let mut targets = Vec::new();
    for item in &selected {
        let resolved = resolve(&root, item)?;
        if !resolved.exists() {
            return Err(missing(&format!("\"{item}\" doesn't exist")));
        }
        let name = resolved
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_else(|| "item".into());
        targets.push((resolved, name));
    }

    let recipient = client
        .map(|axum::Extension(crate::server::ClientId(id))| state.client_label(&id))
        .unwrap_or_else(|| "This PC".to_string());

    let archive_name = if targets.len() == 1 {
        format!("{}.zip", targets[0].1)
    } else {
        format!("{} items.zip", targets.len())
    };

    let transfer_id = crate::model::random_id();
    state.add_transfer(crate::model::Transfer {
        id: transfer_id.clone(),
        name: archive_name.clone(),
        device_id: None,
        device_name: recipient,
        direction: crate::model::TransferDirection::Download,
        state: crate::model::TransferState::Active,
        bytes_done: 0,
        bytes_total: 0,
        rate: None,
        started_at: crate::server::now_iso(),
        finished_at: None,
        error: None,
    });

    let (tx, rx) = tokio::sync::mpsc::channel::<std::io::Result<axum::body::Bytes>>(8);

    // A plain OS thread, not spawn_blocking: zipping a large selection
    // can run for minutes, and tokio's blocking pool is a shared resource
    // that other requests need.
    let zip_state = state.clone();
    let zip_id = transfer_id.clone();
    std::thread::spawn(move || {
        let writer = ChannelWriter { tx: tx.clone(), written: 0 };
        let result = build_zip(writer, targets, &zip_state, &zip_id);

        match result {
            Ok(total) => {
                zip_state.update_transfer_progress(&zip_id, total, None);
                zip_state.finish_transfer(&zip_id, crate::model::TransferState::Done, None);
            }
            Err(err) => {
                // A cancel arrives here as an error too, since that is
                // how the copy loop stops. Reporting it as a failure
                // would put a red row and an error message in front of
                // someone who pressed Cancel and got what they asked for.
                if zip_state.is_cancelled(&zip_id) {
                    zip_state.finish_transfer(
                        &zip_id,
                        crate::model::TransferState::Cancelled,
                        None,
                    );
                } else {
                    zip_state.finish_transfer(
                        &zip_id,
                        crate::model::TransferState::Failed,
                        Some(err.to_string()),
                    );
                }
            }
        }
    });

    let body = Body::from_stream(tokio_stream::wrappers::ReceiverStream::new(rx));

    Ok((
        [
            (header::CONTENT_TYPE, "application/zip".to_string()),
            (
                header::CONTENT_DISPOSITION,
                content_disposition(&archive_name),
            ),
        ],
        body,
    )
        .into_response())
}

fn build_zip(
    writer: ChannelWriter,
    targets: Vec<(PathBuf, String)>,
    state: &SharedState,
    transfer_id: &str,
) -> std::io::Result<u64> {
    use std::io::Write;
    use zip::write::SimpleFileOptions;

    // `new_stream` is the crate's own answer to a non-seekable sink: it
    // writes data descriptors after each entry instead of rewinding to
    // patch headers. Hand-rolling a `Seek` shim for `ZipWriter::new`
    // looked equivalent and wasn't — the writer really does rewind, and
    // the archive died after the first file.
    let mut zip = zip::ZipWriter::new_stream(writer);
    // large_file makes the writer emit a data descriptor after each entry
    // rather than seeking back to patch the header — which is what lets
    // this work over a non-seekable stream at all. It also lifts the 4 GB
    // per-entry ceiling, which matters for exactly the kind of folder
    // someone bulk-downloads.
    // Stored, not Deflated. What people bulk-download is photos, video
    // and archives, all already compressed — deflating those spends a
    // core to save almost nothing and caps the transfer at however fast
    // one thread can compress, well below what the disk and a LAN can
    // do. Storing makes the zip a container rather than a compressor,
    // which is all it needs to be here.
    let options = SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Stored)
        .large_file(true);

    let mut total = 0u64;

    for (path, name) in targets {
        // Also checked here, not only inside `copy_into`: a selection of
        // ten thousand small files spends most of its time between them,
        // and a cancel that only lands mid-file would appear to do
        // nothing at all on exactly that kind of archive.
        if state.is_cancelled(transfer_id) {
            return Err(std::io::Error::new(
                std::io::ErrorKind::Interrupted,
                "cancelled at the PC",
            ));
        }

        if path.is_file() {
            zip.start_file(name, options)?;
            total += copy_into(&mut zip, &path, state, transfer_id, total)?;
        } else {
            for entry in walkdir::WalkDir::new(&path).into_iter().flatten() {
                if !entry.file_type().is_file() {
                    continue;
                }
                let relative = entry
                    .path()
                    .strip_prefix(&path)
                    .map(|p| format!("{name}/{}", p.to_string_lossy().replace('\\', "/")))
                    .unwrap_or_else(|_| name.clone());

                zip.start_file(relative, options)?;
                total += copy_into(&mut zip, entry.path(), state, transfer_id, total)?;
            }
        }
    }

    zip.finish()?.flush()?;
    Ok(total)
}

fn copy_into(
    zip: &mut zip::ZipWriter<zip::write::StreamWriter<ChannelWriter>>,
    path: &std::path::Path,
    state: &SharedState,
    transfer_id: &str,
    already: u64,
) -> std::io::Result<u64> {
    use std::io::{Read, Write};

    let mut file = std::fs::File::open(path)?;
    // Same reasoning as READ_CHUNK: fewer, larger reads. This one is a
    // plain blocking read on a dedicated thread, so the buffer is the
    // whole cost.
    let mut buffer = vec![0u8; READ_CHUNK];
    let mut copied = 0u64;

    loop {
        if state.is_cancelled(transfer_id) {
            return Err(std::io::Error::new(
                std::io::ErrorKind::Interrupted,
                "cancelled at the PC",
            ));
        }

        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        zip.write_all(&buffer[..read])?;
        copied += read as u64;
        state.update_transfer_progress(transfer_id, already + copied, None);
    }

    Ok(copied)
}

/// Bridges the zip writer's blocking `Write` to the async response body.
struct ChannelWriter {
    tx: tokio::sync::mpsc::Sender<std::io::Result<axum::body::Bytes>>,
    written: u64,
}

impl std::io::Write for ChannelWriter {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        // Blocking send applies backpressure: if the device is reading
        // slowly, zipping slows to match instead of buffering ahead.
        self.tx
            .blocking_send(Ok(axum::body::Bytes::copy_from_slice(buf)))
            .map_err(|_| {
                std::io::Error::new(
                    std::io::ErrorKind::BrokenPipe,
                    "the device stopped downloading",
                )
            })?;
        self.written += buf.len() as u64;
        Ok(buf.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}


/// Wraps the file stream so a download reports progress as bytes leave.
///
/// Progress can't be measured at the handler, because the handler returns
/// the instant the response *starts*. The bytes flow afterwards, driven by
/// the client, so the count has to live where the chunks do.
struct TrackedDownload<S> {
    inner: S,
    state: SharedState,
    /// `None` for a range that continues a download already on the list.
    /// Those serve normally but record nothing, so one chunked fetch
    /// stays one row — see `download`.
    id: Option<String>,
    sent: u64,
    /// Expected size. Needed because the stream is often never polled to
    /// exhaustion — see the `Drop` impl.
    total: u64,
    /// Whether a terminal state was already recorded, so `Drop` doesn't
    /// overwrite a completed transfer with a failure.
    settled: bool,
}

impl<S> futures::Stream for TrackedDownload<S>
where
    S: futures::Stream<Item = std::io::Result<axum::body::Bytes>> + Unpin,
{
    type Item = std::io::Result<axum::body::Bytes>;

    fn poll_next(
        self: std::pin::Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Option<Self::Item>> {
        use std::task::Poll;

        let this = self.get_mut();

        // Cancelled at the PC. Ending the body here is the whole effect:
        // the response promised a Content-Length it will now not reach,
        // so the client sees a download that stopped short — which is
        // exactly what cancelling one is.
        if let Some(id) = &this.id {
            if !this.settled && this.state.is_cancelled(id) {
                this.settled = true;
                this.state.finish_transfer(
                    id,
                    crate::model::TransferState::Cancelled,
                    None,
                );
                return Poll::Ready(None);
            }
        }

        match std::pin::Pin::new(&mut this.inner).poll_next(cx) {
            Poll::Ready(Some(Ok(chunk))) => {
                this.sent += chunk.len() as u64;
                if let Some(id) = &this.id {
                    // Throttled inside the store, so per-chunk is cheap.
                    this.state.update_transfer_progress(id, this.sent, None);
                }
                Poll::Ready(Some(Ok(chunk)))
            }
            Poll::Ready(Some(Err(err))) => {
                this.settled = true;
                if let Some(id) = &this.id {
                    this.state.finish_transfer(
                        id,
                        crate::model::TransferState::Failed,
                        Some(err.to_string()),
                    );
                }
                Poll::Ready(Some(Err(err)))
            }
            Poll::Ready(None) => {
                if !this.settled {
                    this.settled = true;
                    if let Some(id) = &this.id {
                        this.state.finish_transfer(
                            id,
                            crate::model::TransferState::Done,
                            None,
                        );
                    }
                }
                Poll::Ready(None)
            }
            Poll::Pending => Poll::Pending,
        }
    }
}

/// Decides the outcome when the stream is dropped.
///
/// Success is judged by bytes delivered, not by reaching end-of-stream.
/// Because the response carries a `Content-Length`, hyper stops polling
/// the moment it has that many bytes and drops the stream without ever
/// returning `None` — so a perfectly good download would otherwise be
/// recorded as failed. An abandoned download reaches here too, but with
/// fewer bytes sent than promised, which is what distinguishes them.
impl<S> Drop for TrackedDownload<S> {
    fn drop(&mut self) {
        if self.settled {
            return;
        }

        let Some(id) = &self.id else { return };

        if self.sent >= self.total {
            self.state
                .finish_transfer(id, crate::model::TransferState::Done, None);
        } else {
            self.state.finish_transfer(
                id,
                crate::model::TransferState::Failed,
                Some("The device stopped downloading before the file finished.".into()),
            );
        }
    }
}

pub async fn upload(
    State(state): State<SharedState>,
    Query(q): Query<PathQuery>,
    client: Option<axum::Extension<crate::server::ClientId>>,
    mut multipart: Multipart,
) -> Result<StatusCode, FileError> {
    let root = state.shared_root();
    let dir = resolve(&root, &q.path)?;

    if !dir.is_dir() {
        return Err(missing("That folder doesn't exist"));
    }

    // Who to credit the transfer to. Absent over loopback, where the
    // sender is this machine itself.
    let sender = client
        .map(|axum::Extension(crate::server::ClientId(id))| state.client_label(&id))
        .unwrap_or_else(|| "This PC".to_string());

    while let Some(mut field) = multipart.next_field().await.map_err(failed)? {
        let Some(filename) = field.file_name().map(str::to_string) else {
            continue;
        };

        // The filename comes from the client, so it goes through the
        // same resolve() as any other path — a folder upload legitimately
        // carries "sub/dir/file.txt", and that must land inside the
        // share, not wherever the name says.
        let rel = format!("{}/{}", q.path.trim_matches('/'), filename);
        let dest = resolve(&root, rel.trim_matches('/'))?;

        if let Some(parent) = dest.parent() {
            tokio::fs::create_dir_all(parent).await.map_err(failed)?;
        }

        // Multipart carries no per-part length, so the total is genuinely
        // unknown until the part ends. Recorded as 0 and rendered as an
        // indeterminate "12.4 MB so far" rather than inventing a
        // percentage that would be wrong.
        let transfer_id = crate::model::random_id();
        state.add_transfer(crate::model::Transfer {
            id: transfer_id.clone(),
            name: filename.clone(),
            device_id: None,
            device_name: sender.clone(),
            direction: crate::model::TransferDirection::Upload,
            state: crate::model::TransferState::Active,
            bytes_done: 0,
            bytes_total: 0,
            rate: None,
            started_at: crate::server::now_iso(),
            finished_at: None,
            error: None,
        });

        let outcome = write_field(&mut field, &dest, &state, &transfer_id).await;

        match outcome {
            Ok(bytes) => {
                state.update_transfer_progress(&transfer_id, bytes, None);
                state.finish_transfer(
                    &transfer_id,
                    crate::model::TransferState::Done,
                    None,
                );
            }
            Err(err) => {
                let message = err.1.clone();
                if state.is_cancelled(&transfer_id) {
                    state.finish_transfer(
                        &transfer_id,
                        crate::model::TransferState::Cancelled,
                        None,
                    );
                } else {
                    state.finish_transfer(
                        &transfer_id,
                        crate::model::TransferState::Failed,
                        Some(message),
                    );
                }
                // A partial file is worse than no file — it looks like a
                // successful transfer until someone opens it.
                let _ = tokio::fs::remove_file(&dest).await;
                return Err(err);
            }
        }
    }

    state.publish();
    Ok(StatusCode::NO_CONTENT)
}

/// Streams one multipart field to disk, reporting progress as it goes.
async fn write_field(
    field: &mut axum::extract::multipart::Field<'_>,
    dest: &std::path::Path,
    state: &SharedState,
    transfer_id: &str,
) -> Result<u64, FileError> {
    // Buffered: multipart hands over whatever came off the socket, often
    // only a few KB, and every unbuffered write is its own trip to
    // tokio's blocking pool. Batching them into 256 KB writes turns
    // thousands of those per second into a handful.
    let file = tokio::fs::File::create(dest).await.map_err(failed)?;
    let mut file = tokio::io::BufWriter::with_capacity(READ_CHUNK, file);
    let mut written = 0u64;

    while let Some(chunk) = field.chunk().await.map_err(failed)? {
        if state.is_cancelled(transfer_id) {
            return Err(bad("Cancelled at the PC."));
        }
        file.write_all(&chunk).await.map_err(failed)?;
        written += chunk.len() as u64;
        // Throttled inside the store, so this is cheap to call per chunk.
        state.update_transfer_progress(transfer_id, written, None);
    }

    file.flush().await.map_err(failed)?;
    Ok(written)
}

pub async fn mkdir(
    State(state): State<SharedState>,
    Query(q): Query<PathQuery>,
) -> Result<StatusCode, FileError> {
    let root = state.shared_root();
    let target = resolve(&root, &q.path)?;

    if target.exists() {
        return Err(FileError(
            StatusCode::CONFLICT,
            "Something with that name already exists".into(),
        ));
    }

    tokio::fs::create_dir_all(&target).await.map_err(failed)?;
    state.publish();
    Ok(StatusCode::NO_CONTENT)
}

pub async fn delete(
    State(state): State<SharedState>,
    Query(q): Query<PathQuery>,
) -> Result<StatusCode, FileError> {
    let root = state.shared_root();
    let target = resolve(&root, &q.path)?;

    // Deleting the share root would wipe everything being shared from
    // one mistaken request. Nothing legitimately needs it.
    if target == root.canonicalize().map_err(failed)? {
        return Err(bad("Can't delete the shared folder itself"));
    }
    if !target.exists() {
        return Err(missing("That item doesn't exist"));
    }

    if target.is_dir() {
        tokio::fs::remove_dir_all(&target).await.map_err(failed)?;
    } else {
        tokio::fs::remove_file(&target).await.map_err(failed)?;
    }

    state.publish();
    Ok(StatusCode::NO_CONTENT)
}

pub async fn rename(
    State(state): State<SharedState>,
    Query(q): Query<RenameQuery>,
) -> Result<StatusCode, FileError> {
    let root = state.shared_root();
    let target = resolve(&root, &q.path)?;

    if !target.exists() {
        return Err(missing("That item doesn't exist"));
    }
    // The new name must be exactly one ordinary path component.
    //
    // Rejecting `/` and `\` alone was not enough. On Windows a
    // drive-relative name like `C:evil.txt` contains neither, and
    // `PathBuf::push` is documented to replace the base outright when
    // its argument carries a prefix but no root — so the destination
    // became `C:evil.txt`, and the rename moved the file clean out of
    // the shared folder. `Component::Normal` is the precise statement of
    // what a name may be: not `..`, not a root, not a drive prefix, and
    // containing no separator of its own.
    let mut parts = Path::new(&q.new_name).components();
    if !matches!(parts.next(), Some(Component::Normal(_))) {
        return Err(bad("That isn't a valid name"));
    }
    if parts.next().is_some() {
        return Err(bad("A name can't contain a path separator"));
    }

    let dest = target
        .parent()
        .ok_or_else(|| bad("Can't rename that"))?
        .join(&q.new_name);

    if dest.exists() {
        return Err(FileError(
            StatusCode::CONFLICT,
            "Something with that name already exists".into(),
        ));
    }

    tokio::fs::rename(&target, &dest).await.map_err(failed)?;
    state.publish();
    Ok(StatusCode::NO_CONTENT)
}
