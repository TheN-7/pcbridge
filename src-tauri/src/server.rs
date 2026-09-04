//! The HTTP surface.
//!
//! Everything the interface does goes through here — including the
//! desktop window, which talks to this server over localhost exactly
//! like a phone does over the network. Keeping one code path instead of
//! branching between Tauri IPC and HTTP is what guarantees the desktop
//! and a phone can't drift apart in behavior.
//!
//! `/events` is the spine of that: a Server-Sent Events stream that
//! pushes a complete snapshot whenever anything changes.

use std::convert::Infallible;
use std::net::SocketAddr;
use std::time::Duration;

use axum::extract::{DefaultBodyLimit, Path, Query, State};
use axum::http::StatusCode;
use axum::middleware;
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use futures::stream::{self, Stream, StreamExt};
use serde::Deserialize;
use tokio_stream::wrappers::BroadcastStream;
use tower_http::cors::CorsLayer;

use crate::files;
use crate::model::{NetworkMode, SettingsPatch};
use crate::store::SharedState;

/// Builds the API.
///
/// `require_pin` is the only difference between what the desktop window
/// gets over loopback and what a phone gets over TLS. The routes are
/// otherwise identical, deliberately: a behavior that only exists on one
/// of the two surfaces is a behavior that will eventually diverge.
pub fn router(state: SharedState, require_pin: bool) -> Router {
    // `require_pin` doubles as "this is the network listener". Browsers
    // get a strictly smaller API than the desktop window: the window is
    // reachable only over loopback, which already proves you're sitting
    // at this machine, while anything on the network is a stranger until
    // someone here says otherwise.
    //
    // The split is enforced here, not in the interface. Hiding a button
    // is a suggestion; not routing the endpoint is a rule.
    let api = if require_pin { browser_routes() } else { api_routes() };

    // Only the network listener tracks clients. Requests over loopback
    // come from this machine's own window, which isn't a "connected
    // device" in any sense the Devices screen should report.
    // Order matters and is easy to get backwards: the *last* layer added
    // is the outermost, so `track_client` must be added after `check_pin`
    // to run before it. That's deliberate — a device repeatedly failing
    // the PIN is exactly what the Devices screen should surface, and
    // tracking it only after authentication would hide it completely.
    let api = if require_pin {
        api.layer(middleware::from_fn_with_state(state.clone(), require_session))
            .layer(middleware::from_fn_with_state(state.clone(), track_client))
    } else {
        api
    };

    // Attached *after* the PIN layer, so the interface itself is served
    // without one. It's inert HTML and JavaScript carrying no data, and a
    // phone has to be able to load the PIN screen before it can possibly
    // know the PIN. Every route that touches your files stays gated.
    api.fallback(static_asset).with_state(state)
}

/// The built web interface, compiled into the binary.
///
/// In debug builds rust-embed reads from disk, so frontend changes show
/// up without recompiling Rust. Release builds embed the files, which is
/// what keeps this a single self-contained executable.
#[derive(rust_embed::Embed)]
#[folder = "../build"]
struct Assets;

async fn static_asset(uri: axum::http::Uri) -> Response {
    let path = uri.path().trim_start_matches('/');

    // An unknown API path is a mistake, not a page. Falling through to
    // index.html would hand callers a 200 and a lump of HTML where they
    // expected JSON — which silently turns a typo into "the field is
    // missing" instead of "that route doesn't exist".
    if path.starts_with("api/") || path == "events" {
        return (StatusCode::NOT_FOUND, "No such endpoint").into_response();
    }

    let path = if path.is_empty() { "index.html" } else { path };

    if let Some(file) = Assets::get(path) {
        return serve_embedded(path, file);
    }

    // Unknown path falls back to index.html so client-side routing works:
    // opening /devices directly on a phone must land on the app, not a
    // 404.
    match Assets::get("index.html") {
        Some(index) => serve_embedded("index.html", index),
        None => (
            StatusCode::NOT_FOUND,
            "The interface hasn't been built yet. Run `npm run build`.",
        )
            .into_response(),
    }
}

fn serve_embedded(path: &str, file: rust_embed::EmbeddedFile) -> Response {
    let mime = mime_guess::from_path(path).first_or_octet_stream();
    (
        [(axum::http::header::CONTENT_TYPE, mime.as_ref().to_string())],
        file.data.into_owned(),
    )
        .into_response()
}

/// Everything a browser is allowed to reach.
///
/// Read plus upload. No delete, rename, or new folder — a borrowed or
/// unlocked phone can't destroy anything — and nothing that touches the
/// app itself: no settings, no device list, no transfer controls.
fn browser_routes() -> Router<SharedState> {
    Router::new()
        // Exempt from the session check (see `require_session`): this is
        // where a session is obtained.
        .route("/api/session", post(open_session))
        .route("/api/session", get(session_status))
        // Held open for as long as the browsing tab is on screen, purely
        // so the Devices screen can tell "connected now" from "seen
        // recently" — see `session_events`. It carries no data, unlike
        // `/events`, which browsers are never given access to.
        .route("/api/session/events", get(session_events))
        .route("/api/files/list", get(files::list))
        .route("/api/files/download", get(files::download))
        .route("/api/files/download-zip", get(files::download_zip))
        .route(
            "/api/files/upload",
            post(files::upload).layer(DefaultBodyLimit::disable()),
        )
        .layer(CorsLayer::permissive())
}

fn api_routes() -> Router<SharedState> {
    Router::new()
        .route("/api/health", get(health))
        // The same payload the event stream pushes, fetched once. Useful
        // for anything that can't hold a stream open, and for checking
        // state without subscribing.
        .route("/api/snapshot", get(snapshot))
        .route("/events", get(events))
        .route("/api/files/list", get(files::list))
        .route("/api/files/download", get(files::download))
        // axum caps request bodies at 2 MB by default. That's a sensible
        // guard for JSON, and completely wrong for a file upload — it
        // silently truncated anything larger, which is nearly every file
        // worth sending. Lifted only on this route: the handler streams
        // straight to disk and never buffers the body, so there's no
        // memory to exhaust. Every other route keeps the default.
        .route(
            "/api/files/upload",
            post(files::upload).layer(DefaultBodyLimit::disable()),
        )
        .route("/api/files/mkdir", post(files::mkdir))
        .route("/api/files/delete", post(files::delete))
        .route("/api/files/rename", post(files::rename))
        .route("/api/serving", post(set_serving))
        .route("/api/settings", post(patch_settings))
        .route("/api/settings/pin/regenerate", post(regenerate_pin))
        .route("/api/pair-code", post(new_pair_code))
        .route("/api/pair-qr", get(pair_qr))
        .route("/api/sessions/{id}/resolve", post(resolve_session))
        .route("/api/remembered/{id}/forget", post(forget_device))
        .route("/api/transfers/{id}/cancel", post(cancel_transfer))
        .route("/api/transfers/clear-finished", post(clear_finished))
        // Phones open this page from one PC while talking to another, so
        // cross-origin requests have to be possible at all. Permission to
        // *send* a request is not permission to act: every request that
        // touches files is still PIN-checked.
        .layer(CorsLayer::permissive())
}

async fn health() -> &'static str {
    "ok"
}

async fn snapshot(State(state): State<SharedState>) -> Response {
    (
        [(axum::http::header::CONTENT_TYPE, "application/json")],
        state.snapshot_json(),
    )
        .into_response()
}

/// Gates every browser request on an approved session.
///
/// The session id travels as a `sid` query parameter or an `X-Session`
/// header. The query form exists because a browser download or an
/// `EventSource` cannot set headers — without it, downloading a file
/// from a phone would need a second token scheme invented for it.
///
/// The PIN is checked once, when a session is opened — not on every
/// request. That's deliberate: it keeps the PIN out of download URLs
/// (which end up in browser history and server logs), and it means
/// revoking a device takes effect immediately rather than only after the
/// PIN is changed for everyone.
async fn require_session(
    State(state): State<SharedState>,
    request: axum::extract::Request,
    next: middleware::Next,
) -> Response {
    // Opening a session is the one thing you can do without one.
    if request.uri().path() == "/api/session" && request.method() == axum::http::Method::POST {
        return next.run(request).await;
    }

    let sid = query_value(request.uri().query(), "sid")
        .or_else(|| {
            request
                .headers()
                .get("x-session")
                .and_then(|v| v.to_str().ok())
                .map(str::to_string)
        })
        .unwrap_or_default();

    if state.session_is_approved(&sid) {
        return next.run(request).await;
    }

    // 403 rather than 401: the credentials may well be right, but this
    // device hasn't been allowed in. The interface distinguishes them.
    match state.session(&sid).map(|s| s.status) {
        Some(crate::model::SessionStatus::Pending) => (
            StatusCode::FORBIDDEN,
            "Waiting for approval on the PC.",
        )
            .into_response(),
        Some(crate::model::SessionStatus::Denied) => {
            (StatusCode::FORBIDDEN, "This device was not allowed.").into_response()
        }
        _ => (StatusCode::UNAUTHORIZED, "Enter the PIN to connect.").into_response(),
    }
}

/// Every value for a repeated query key, e.g. `?path=a&path=b`.
///
/// Hand-parsed because `serde_urlencoded` — what axum's `Query` is built
/// on — silently refuses repeated keys rather than collecting them into a
/// sequence, which is exactly the shape a multi-select download needs.
pub(crate) fn query_values(query: Option<&str>, key: &str) -> Vec<String> {
    let Some(query) = query else { return Vec::new() };
    query
        .split('&')
        .filter_map(|pair| pair.split_once('='))
        .filter(|(k, _)| *k == key)
        .map(|(_, v)| percent_decode(v))
        .filter(|v| !v.is_empty())
        .collect()
}

fn query_value(query: Option<&str>, key: &str) -> Option<String> {
    query?
        .split('&')
        .filter_map(|pair| pair.split_once('='))
        .find(|(k, _)| *k == key)
        .map(|(_, v)| percent_decode(v))
}

fn percent_decode(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => {
                let hex = std::str::from_utf8(&bytes[i + 1..i + 3]).unwrap_or("");
                match u8::from_str_radix(hex, 16) {
                    Ok(byte) => {
                        out.push(byte);
                        i += 3;
                    }
                    Err(_) => {
                        out.push(bytes[i]);
                        i += 1;
                    }
                }
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            byte => {
                out.push(byte);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct OpenSessionBody {
    /// Absent when a pairing code is used instead.
    #[serde(default)]
    pin: String,
    /// A code scanned from the QR on the PC's screen. Stands in for the
    /// PIN, and settles the approval too -- see `open_session`.
    #[serde(default)]
    pair_code: Option<String>,
    /// The key this browser kept from a previous visit, if any. Absent
    /// on a device's first connection, and after the user clears site
    /// data — both of which simply mean it waits for approval again.
    #[serde(default)]
    device_key: Option<String>,
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct SessionReply {
    session_id: String,
    /// Store this and send it back next time. It is what makes
    /// "remember this device" survive a change of address.
    ///
    /// Only present on the reply that opens a session, which is where a
    /// key is issued. The status poll leaves it out rather than handing
    /// back a second key the client would overwrite the first with.
    #[serde(skip_serializing_if = "Option::is_none")]
    device_key: Option<String>,
    status: crate::model::SessionStatus,
}

/// Exchanges a correct PIN for a session, which then waits for approval.
async fn open_session(
    State(state): State<SharedState>,
    client: Option<axum::Extension<ClientId>>,
    Json(body): Json<OpenSessionBody>,
) -> Response {
    let Some(axum::Extension(ClientId(client_id))) = client else {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            "Could not identify this device.",
        )
            .into_response();
    };

    let label = state.client_label(&client_id);
    let address = state.client_address(&client_id);

    // A scanned pairing code stands in for the PIN. It was on this
    // machine's own screen seconds ago and is good once, which is a
    // stronger claim than knowing a PIN that never changes — so it also
    // settles the approval that a PIN alone only starts.
    let paired = match body.pair_code.as_deref() {
        Some(code) if !code.is_empty() => match state.take_pair_code(code) {
            Some(remember) => Some(remember),
            None => {
                return (
                    StatusCode::UNAUTHORIZED,
                    "That pairing code has expired. Show a new one on the PC.",
                )
                    .into_response()
            }
        },
        _ => None,
    };

    if paired.is_none() {
        // Checked before the PIN, so a caller that is already being made
        // to wait gains nothing by guessing again — and so a correct
        // guess arriving mid-backoff doesn't skip the queue.
        if let Some(wait) = state.pin_retry_delay(&address) {
            let secs = wait.as_secs().max(1);
            return (
                StatusCode::TOO_MANY_REQUESTS,
                [(axum::http::header::RETRY_AFTER, secs.to_string())],
                format!("Too many wrong PINs. Try again in {secs}s."),
            )
                .into_response();
        }

        let expected = state.settings().pin;
        if !constant_time_eq(body.pin.as_bytes(), expected.as_bytes()) {
            state.record_pin_failure(&address);
            return (StatusCode::UNAUTHORIZED, "That PIN wasn't accepted.").into_response();
        }
        state.clear_pin_failures(&address);
    }

    let (session, device_key) =
        state.begin_session(&label, &address, body.device_key.as_deref());

    // Pairing approves outright, and remembers if that is what the person
    // holding the PC chose when they displayed the code. Reusing
    // resolve_session rather than approving inline keeps one path for
    // "this device is allowed in", including the part that also approves
    // any other session the same device already had waiting.
    let status = if let Some(remember) = paired {
        let _ = state.resolve_session(&session.id, true, remember);
        crate::model::SessionStatus::Approved
    } else {
        session.status
    };

    Json(SessionReply {
        session_id: session.id,
        device_key: Some(device_key),
        status,
    })
    .into_response()
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct PairCodeBody {
    /// Whether scanning should also remember the device. Decided here,
    /// on the PC, rather than by whatever scans the code.
    #[serde(default)]
    remember: bool,
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct PairCodeReply {
    /// What the QR encodes. `None` when this machine has no LAN address
    /// to be reached at, in which case there is nothing to pair over.
    url: Option<String>,
    expires_in_seconds: u64,
}

/// Puts a fresh pairing code on screen. Owner-only by virtue of living
/// in `api_routes`, which browsers never reach.
async fn new_pair_code(
    State(state): State<SharedState>,
    Json(body): Json<PairCodeBody>,
) -> Response {
    let (code, ttl) = state.mint_pair_code(body.remember);
    Json(PairCodeReply {
        url: state.pair_url(&code),
        expires_in_seconds: ttl,
    })
    .into_response()
}

/// The current pairing code as a QR image.
///
/// Served rather than embedded in the snapshot so the code itself never
/// has to travel through the state broadcast, and so the interface can
/// simply point an `<img>` at it.
async fn pair_qr(State(state): State<SharedState>) -> Response {
    let Some(code) = state.current_pair_code() else {
        return (StatusCode::NOT_FOUND, "No pairing code is on screen.").into_response();
    };
    let Some(url) = state.pair_url(&code) else {
        return (
            StatusCode::CONFLICT,
            "This PC isn't on a network a device could reach it on.",
        )
            .into_response();
    };

    match qr_svg(&url) {
        Some(svg) => (
            [
                (axum::http::header::CONTENT_TYPE, "image/svg+xml"),
                (axum::http::header::CACHE_CONTROL, "no-store"),
            ],
            svg,
        )
            .into_response(),
        None => (
            StatusCode::INTERNAL_SERVER_ERROR,
            "Couldn't draw that pairing code.",
        )
            .into_response(),
    }
}

/// A QR for `data`, as SVG built straight from the module matrix.
///
/// Drawn by hand rather than with the crate's own renderer so its `svg`
/// and `image` features can stay off — they pull in a considerably
/// larger tree than a few hundred rectangles are worth. No fixed pixel
/// size either: the viewBox lets the page scale it to whatever room it
/// has without the edges going soft.
fn qr_svg(data: &str) -> Option<String> {
    use qrcode::{Color, QrCode};

    let code = QrCode::new(data.as_bytes()).ok()?;
    let width = code.width();
    let colors = code.to_colors();

    // Four modules of quiet zone, as the spec requires. Scanners are
    // much less reliable without it, especially against a dark UI.
    const QUIET: usize = 4;
    let side = width + QUIET * 2;

    let mut svg = format!(
        "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 {side} {side}\" \
         shape-rendering=\"crispEdges\">\
         <rect width=\"{side}\" height=\"{side}\" fill=\"#fff\"/>"
    );

    for (i, color) in colors.iter().enumerate() {
        if matches!(color, Color::Dark) {
            let x = i % width + QUIET;
            let y = i / width + QUIET;
            svg.push_str(&format!(
                "<rect x=\"{x}\" y=\"{y}\" width=\"1\" height=\"1\"/>"
            ));
        }
    }

    svg.push_str("</svg>");
    Some(svg)
}

/// Polled by a waiting browser until it's allowed in or turned away.
async fn session_status(
    State(state): State<SharedState>,
    Query(q): Query<SessionQuery>,
) -> Response {
    match state.session(&q.sid) {
        Some(session) => Json(SessionReply {
            session_id: session.id,
            device_key: None,
            status: session.status,
        })
        .into_response(),
        None => (StatusCode::NOT_FOUND, "No such session.").into_response(),
    }
}

#[derive(Deserialize)]
struct SessionQuery {
    #[serde(default)]
    sid: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ResolveBody {
    approve: bool,
    #[serde(default)]
    remember: bool,
}

async fn resolve_session(
    State(state): State<SharedState>,
    Path(id): Path<String>,
    Json(body): Json<ResolveBody>,
) -> Result<StatusCode, (StatusCode, String)> {
    state
        .resolve_session(&id, body.approve, body.remember)
        .map(|_| StatusCode::NO_CONTENT)
        .map_err(|err| (StatusCode::NOT_FOUND, err.to_string()))
}

async fn forget_device(
    State(state): State<SharedState>,
    Path(id): Path<String>,
) -> Result<StatusCode, (StatusCode, String)> {
    state.forget_device(&id).map(|_| StatusCode::NO_CONTENT).map_err(internal)
}


/// Notes which browser made this request, and hands the resulting id to
/// the handler through request extensions so the event stream can mark
/// itself live without recomputing it.
///
/// Layered *outside* the PIN check, so a device that keeps trying with a
/// wrong PIN still shows up. Someone hammering your server is precisely
/// the thing you'd want the Devices screen to reveal.
async fn track_client(
    State(state): State<SharedState>,
    axum::extract::ConnectInfo(peer): axum::extract::ConnectInfo<SocketAddr>,
    mut request: axum::extract::Request,
    next: middleware::Next,
) -> Response {
    let address = peer.ip().to_string();

    let agent = request
        .headers()
        .get(axum::http::header::USER_AGENT)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();

    let id = state.touch_client(&address, &agent);
    request.extensions_mut().insert(ClientId(id));

    next.run(request).await
}

#[derive(Clone)]
pub struct ClientId(pub String);

/// Decrements the stream count when the event stream is dropped —
/// whether the browser closed the tab, slept, or lost Wi-Fi. Tying it to
/// `Drop` rather than a disconnect callback is what makes it correct for
/// all three: there's no path where the stream ends without this running.
struct StreamGuard {
    state: SharedState,
    id: String,
}

impl Drop for StreamGuard {
    fn drop(&mut self) {
        self.state.client_stream_closed(&self.id);
    }
}

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// Sends the current snapshot immediately on connect, then one per
/// change. The immediate send matters: a window that opens midway
/// through a session must not sit blank waiting for something to happen.
async fn events(
    State(state): State<SharedState>,
    client: Option<axum::Extension<ClientId>>,
) -> Sse<impl Stream<Item = Result<Event, Infallible>>> {
    // Present only on the network listener; the local window isn't
    // tracked as a connected device.
    let guard = client.map(|axum::Extension(ClientId(id))| {
        state.client_stream_opened(&id);
        StreamGuard { state: state.clone(), id }
    });

    // Subscribe *before* taking the snapshot. Done the other way round,
    // a change landing between the snapshot and the subscription would
    // be missed entirely, leaving this client silently stale until the
    // next unrelated change. Subscribing first can only duplicate an
    // update, and a snapshot is a complete picture, so applying one
    // twice is harmless.
    let receiver = state.subscribe();
    let initial = stream::once(async move { state.snapshot_json() });
    let updates = BroadcastStream::new(receiver).filter_map(|msg| async move {
        // A lagged receiver means this client fell behind and missed
        // messages. Dropping the error is right: the next snapshot is
        // complete, so it recovers on its own without a resync protocol.
        msg.ok()
    });

    // The guard is moved into the stream so its lifetime is the stream's
    // lifetime — when the client disconnects and the stream is dropped,
    // the guard drops with it and the device leaves the list.
    let stream = initial.chain(updates).map(move |json| {
        let _keep = &guard;
        Ok(Event::default().data(json))
    });

    Sse::new(stream).keep_alive(
        KeepAlive::new()
            .interval(Duration::from_secs(15))
            .text("keep-alive"),
    )
}

/// A browser's counterpart to `events` — held open for exactly as long as
/// its tab is on screen, purely so this device counts toward `streams`
/// while it's genuinely there.
///
/// It never carries a payload. `events` streams the full snapshot, which
/// is exactly what a browser must not see: settings, the PIN, other
/// devices, transfer history. This connection exists only for its
/// lifetime, not its content — nothing is ever sent down it besides the
/// SSE keep-alive comment, and nothing needs to be, since the guard that
/// keeps the stream count accurate lives and dies with the connection
/// either way.
async fn session_events(
    State(state): State<SharedState>,
    client: Option<axum::Extension<ClientId>>,
) -> Sse<impl Stream<Item = Result<Event, Infallible>>> {
    let guard = client.map(|axum::Extension(ClientId(id))| {
        state.client_stream_opened(&id);
        StreamGuard { state: state.clone(), id }
    });

    // A stream that never resolves. It doesn't need to: `keep_alive`
    // handles the wire protocol on its own timer, and the guard is kept
    // alive simply by living inside this stream's state for as long as
    // the connection does — the same trick `events` uses above.
    let stream = stream::pending::<Result<Event, Infallible>>().map(move |item| {
        let _keep = &guard;
        item
    });

    Sse::new(stream).keep_alive(
        KeepAlive::new()
            .interval(Duration::from_secs(15))
            .text("keep-alive"),
    )
}

// ------------------------------------------------------------ actions

#[derive(Deserialize)]
struct ServingBody {
    serving: bool,
}

async fn set_serving(
    State(state): State<SharedState>,
    Json(body): Json<ServingBody>,
) -> StatusCode {
    state.set_serving(body.serving);
    StatusCode::NO_CONTENT
}

async fn patch_settings(
    State(state): State<SharedState>,
    Json(patch): Json<SettingsPatch>,
) -> Result<StatusCode, (StatusCode, String)> {
    state
        .apply_settings(patch)
        .map(|_| StatusCode::NO_CONTENT)
        .map_err(internal)
}

async fn regenerate_pin(
    State(state): State<SharedState>,
) -> Result<StatusCode, (StatusCode, String)> {
    state.regenerate_pin().map(|_| StatusCode::NO_CONTENT).map_err(internal)
}

pub fn now_iso() -> String {
    // Seconds since the epoch is enough for "last seen", and avoids
    // pulling in a date library for one field the interface formats
    // itself anyway.
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    secs.to_string()
}

/// Same representation as `now_iso`, for a specific instant.
pub fn iso_from_unix(secs: u64) -> String {
    secs.to_string()
}

async fn cancel_transfer(
    State(state): State<SharedState>,
    Path(id): Path<String>,
) -> StatusCode {
    state.cancel_transfer(&id);
    StatusCode::NO_CONTENT
}

async fn clear_finished(State(state): State<SharedState>) -> StatusCode {
    state.clear_finished_transfers();
    StatusCode::NO_CONTENT
}

/// Errors reach the interface as plain text, which the store surfaces
/// verbatim — so a failure says what went wrong instead of "request
/// failed (500)".
fn internal(err: anyhow::Error) -> (StatusCode, String) {
    (StatusCode::INTERNAL_SERVER_ERROR, err.to_string())
}

// ------------------------------------------------------------- listen

/// Binds the local HTTP listener the desktop window and dev server use.
///
/// Deliberately bound to loopback, not `0.0.0.0`. These routes carry the
/// PIN in every snapshot and are not themselves PIN-checked — reaching
/// them is meant to imply you are already on this machine. Phones get
/// the pinned-HTTPS listener instead, which is authenticated; that is
/// the next piece, and it serves this same router.
///
/// If this ever needs to listen on a real interface, it must gain PIN
/// checking in the same change, not after it.
pub async fn serve_http(state: SharedState, port: u16) -> anyhow::Result<()> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let listener = tokio::net::TcpListener::bind(addr).await?;
    tracing::info!("local api listening on http://{addr}");
    axum::serve(listener, router(state, false)).await?;
    Ok(())
}

/// The listener phones and other PCs connect to.
///
/// Serves the same routes as the loopback listener, with two additions
/// that make it safe to expose: TLS using this machine's own self-signed
/// certificate, and a PIN check on every request.
///
/// No certificate authority will vouch for a private LAN address, so the
/// client's protection isn't a signature — it's pinning this exact
/// certificate's fingerprint on first connect and refusing anything else
/// afterwards. Same model SSH uses for host keys.
pub async fn serve_https(
    state: SharedState,
    port: u16,
    cert: std::path::PathBuf,
    key: std::path::PathBuf,
) -> anyhow::Result<()> {
    supervise_network(state, port, cert, key).await
}

/// Runs the network listener, restarting it whenever the scheme changes.
///
/// Both schemes go through `axum_server` purely so they share a `Handle`,
/// which is what makes an in-place switch possible: the old listener is
/// asked to shut down gracefully, in-flight requests are given a moment
/// to finish, and the new one binds the same port. Without a common
/// shutdown mechanism this would have to be "restart the app to apply",
/// which isn't a toggle in any useful sense.
async fn supervise_network(
    state: SharedState,
    port: u16,
    cert: std::path::PathBuf,
    key: std::path::PathBuf,
) -> anyhow::Result<()> {
    let mut modes = state.subscribe_mode();

    loop {
        let mode = *modes.borrow_and_update();
        let addr = SocketAddr::from(([0, 0, 0, 0], port));
        let handle = axum_server::Handle::new();
        // Connect info is what gives `track_client` the peer address.
        // Without it every browser would look like the same anonymous
        // device.
        let service = router(state.clone(), true)
            .into_make_service_with_connect_info::<SocketAddr>();

        tracing::info!("device api listening on {}://{addr}", mode.scheme());

        let mut serving = {
            let handle = handle.clone();
            let cert = cert.clone();
            let key = key.clone();
            tokio::spawn(async move {
                match mode {
                    NetworkMode::Https => {
                        let config =
                            axum_server::tls_rustls::RustlsConfig::from_pem_file(cert, key)
                                .await?;
                        axum_server::bind_rustls(addr, config)
                            .handle(handle)
                            .serve(service)
                            .await?;
                    }
                    NetworkMode::Http => {
                        axum_server::bind(addr).handle(handle).serve(service).await?;
                    }
                }
                Ok::<_, anyhow::Error>(())
            })
        };

        tokio::select! {
            result = modes.changed() => {
                if result.is_err() {
                    // The store is gone, so nothing will ever ask for
                    // another switch. Keep serving rather than tearing
                    // the listener down for no reason.
                    return serving.await?;
                }
                handle.graceful_shutdown(Some(Duration::from_secs(2)));
                let _ = serving.await;
                // Give the OS a moment to release the port before
                // rebinding it, or the new listener races the old socket
                // and fails with "address in use".
                tokio::time::sleep(Duration::from_millis(250)).await;
            }
            outcome = &mut serving => {
                // The listener stopped on its own — a bound port, or a
                // certificate that couldn't be read. Report it and stop;
                // the window stays usable so the setting can be changed.
                return outcome?;
            }
        }
    }
}
