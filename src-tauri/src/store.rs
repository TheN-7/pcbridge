//! The authoritative application state.
//!
//! This is the "one source" half of "one source, many windows". Nothing
//! else owns a copy: the interface renders whatever the most recent
//! snapshot said, and every mutation goes through `AppState`, which then
//! pushes a fresh snapshot to every connected client.
//!
//! Because the push is a broadcast, the window that made a change learns
//! about it the same way every other window does. There is no path where
//! one surface updates and another doesn't.

use std::net::IpAddr;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant, SystemTime};

use anyhow::Result;
use tokio::sync::broadcast;

use crate::model::*;
use crate::tls::Identity;

pub struct AppState {
    pub data_dir: PathBuf,
    pub identity: Identity,

    settings: RwLock<Settings>,
    transfers: RwLock<Vec<Transfer>>,
    serving: RwLock<bool>,

    /// Snapshots are broadcast as pre-serialized JSON so a slow client
    /// can't make every other client wait on serialization.
    tx: broadcast::Sender<String>,

    /// Rate limit for transfer progress pushes. See
    /// `update_transfer_progress`.
    last_progress_publish: Mutex<Instant>,

    /// Browsers currently talking to us, keyed by a stable id derived
    /// from address + user agent.
    clients: RwLock<std::collections::HashMap<String, ClientRecord>>,

    /// Live sessions, keyed by session id. Not persisted: a restart
    /// should re-ask for anything the user didn't choose to remember.
    sessions: RwLock<std::collections::HashMap<String, Session>>,

    /// Devices allowed in permanently.
    remembered: RwLock<Vec<RememberedDevice>>,

    /// Tells the network listener to restart under a different scheme.
    /// A watch channel rather than a broadcast: only the latest value
    /// matters, and a listener that missed an intermediate flip should
    /// still end up in the right final state.
    mode_tx: tokio::sync::watch::Sender<NetworkMode>,
}

pub type SharedState = Arc<AppState>;

/// How long a quiet browser stays listed before it's dropped.
///
/// Long enough that switching apps on a phone for a moment doesn't make
/// it vanish, short enough that the list still means "right now". A
/// client with an open event stream is never pruned regardless.
const CLIENT_TTL: Duration = Duration::from_secs(90);

struct ClientRecord {
    label: String,
    address: String,
    connected_at: SystemTime,
    last_seen: Instant,
    streams: u32,
}

impl AppState {
    pub fn load(data_dir: PathBuf, identity: Identity) -> Result<SharedState> {
        std::fs::create_dir_all(&data_dir)?;

        let stored: Option<Settings> = read_json(&data_dir.join("settings.json"));
        let first_run = stored.is_none();
        let settings = stored.unwrap_or_default();

        let remembered: Vec<RememberedDevice> =
            read_json(&data_dir.join("remembered.json")).unwrap_or_default();

        let (tx, _) = broadcast::channel(64);
        let (mode_tx, _) = tokio::sync::watch::channel(settings.network_mode);

        let state = Arc::new(Self {
            data_dir,
            identity,
            settings: RwLock::new(settings),
            transfers: RwLock::new(Vec::new()),
            serving: RwLock::new(false),
            tx,
            last_progress_publish: Mutex::new(Instant::now()),
            clients: RwLock::new(std::collections::HashMap::new()),
            sessions: RwLock::new(std::collections::HashMap::new()),
            remembered: RwLock::new(remembered),
            mode_tx,
        });

        // Defaults include a freshly generated PIN. Writing them out on
        // first run is what stops that PIN being regenerated on every
        // launch — which would silently invalidate the PIN on every
        // paired device each time the app restarted.
        if first_run {
            state.persist_settings()?;
        }

        // The shared folder must exist before anything tries to list it.
        // Creating it here rather than erroring keeps first launch silent
        // and working; if it was deleted since, this quietly restores it
        // instead of leaving the Files screen permanently broken.
        let share = state.shared_root();
        if !share.exists() {
            if let Err(err) = std::fs::create_dir_all(&share) {
                tracing::warn!("could not create shared folder {share:?}: {err}");
            }
        }

        Ok(state)
    }

    pub fn subscribe(&self) -> broadcast::Receiver<String> {
        self.tx.subscribe()
    }

    // ---- reads ------------------------------------------------------

    pub fn settings(&self) -> Settings {
        self.settings.read().unwrap().clone()
    }

    pub fn shared_root(&self) -> PathBuf {
        PathBuf::from(self.settings.read().unwrap().shared_folder.clone())
    }

    pub fn snapshot(&self) -> Snapshot {
        Snapshot {
            serving: *self.serving.read().unwrap(),
            settings: self.settings.read().unwrap().clone(),
            server: self.server_info(),
            transfers: self.transfers.read().unwrap().clone(),
            clients: self.client_views(),
            pending_sessions: self.pending_sessions(),
            remembered_devices: self.remembered.read().unwrap().clone(),
        }
    }

    pub fn snapshot_json(&self) -> String {
        serde_json::to_string(&self.snapshot()).unwrap_or_else(|_| "{}".into())
    }

    fn server_info(&self) -> ServerInfo {
        let settings = self.settings.read().unwrap();
        let (lan, tailscale) = detect_addresses();
        let (free, total) = disk_usage_for(Path::new(&settings.shared_folder));

        ServerInfo {
            hostname: hostname::get()
                .ok()
                .and_then(|h| h.into_string().ok())
                .unwrap_or_else(|| "this-pc".into()),
            platform: platform_name(),
            lan_address: lan.map(|ip| format!("{ip}:{}", settings.https_port)),
            tailscale_address: tailscale.map(|ip| format!("{ip}:{}", settings.https_port)),
            fingerprint: self.identity.fingerprint.clone(),
            storage_free: free,
            storage_total: total,
        }
    }

    // ---- writes -----------------------------------------------------
    //
    // Each of these ends in `publish()`. That is the only way state
    // reaches the interface, which is what keeps every window in step.

    pub fn set_serving(&self, serving: bool) {
        *self.serving.write().unwrap() = serving;
        self.publish();
    }

    /// Records that a browser just made a request. Returns its id.
    ///
    /// Called on every request, so it must stay cheap and must not
    /// publish — a client polling would otherwise broadcast a snapshot to
    /// everyone on each poll.
    pub fn touch_client(&self, address: &str, agent: &str) -> String {
        let id = client_id(address, agent);
        let mut clients = self.clients.write().unwrap();

        clients
            .entry(id.clone())
            .and_modify(|c| c.last_seen = Instant::now())
            .or_insert_with(|| ClientRecord {
                label: crate::model::describe_agent(agent),
                address: address.to_string(),
                connected_at: SystemTime::now(),
                last_seen: Instant::now(),
                streams: 0,
            });

        id
    }

    /// An event stream opened. Publishes, because a device appearing in
    /// the list is exactly the kind of change other screens want to see.
    pub fn client_stream_opened(&self, id: &str) {
        {
            let mut clients = self.clients.write().unwrap();
            if let Some(c) = clients.get_mut(id) {
                c.streams += 1;
                c.last_seen = Instant::now();
            }
        }
        self.publish();
    }

    pub fn client_stream_closed(&self, id: &str) {
        {
            let mut clients = self.clients.write().unwrap();
            if let Some(c) = clients.get_mut(id) {
                c.streams = c.streams.saturating_sub(1);
                c.last_seen = Instant::now();
            }
        }
        self.publish();
    }

    /// Drops clients that have gone quiet. Returns true if anything
    /// changed, so the caller only publishes when there's news.
    /// Friendly name for a client, for attributing a transfer to whoever
    /// caused it. Falls back rather than failing: a transfer with an
    /// unknown sender is still worth showing.
    pub fn client_label(&self, id: &str) -> String {
        self.clients
            .read()
            .unwrap()
            .get(id)
            .map(|c| c.label.clone())
            .unwrap_or_else(|| "A device".to_string())
    }

    pub fn client_address(&self, id: &str) -> String {
        self.clients
            .read()
            .unwrap()
            .get(id)
            .map(|c| c.address.clone())
            .unwrap_or_else(|| "unknown".to_string())
    }

    pub fn prune_clients(&self) -> bool {
        let mut clients = self.clients.write().unwrap();
        let before = clients.len();
        clients.retain(|_, c| c.streams > 0 || c.last_seen.elapsed() < CLIENT_TTL);
        clients.len() != before
    }

    fn client_views(&self) -> Vec<ConnectedClient> {
        let clients = self.clients.read().unwrap();
        let mut list: Vec<_> = clients
            .iter()
            .map(|(id, c)| ConnectedClient {
                id: id.clone(),
                label: c.label.clone(),
                address: c.address.clone(),
                connected_at: iso(c.connected_at),
                last_seen: iso(SystemTime::now() - c.last_seen.elapsed()),
                streams: c.streams,
            })
            .collect();

        // Live streams first, then most recently active — so whoever is
        // actually looking at your files is at the top.
        list.sort_by(|a, b| {
            b.streams
                .cmp(&a.streams)
                .then_with(|| b.last_seen.cmp(&a.last_seen))
        });
        list
    }

    // ---- sessions ---------------------------------------------------

    /// Starts a session for a device that gave the right PIN.
    ///
    /// A remembered device is approved immediately; anything else waits
    /// for a person at the PC. Returns the session.
    pub fn begin_session(&self, device_id: &str, label: &str, address: &str) -> Session {
        let remembered = self
            .remembered
            .read()
            .unwrap()
            .iter()
            .any(|d| d.device_id == device_id);

        let session = Session {
            id: random_id(),
            device_id: device_id.to_string(),
            label: label.to_string(),
            address: address.to_string(),
            status: if remembered {
                SessionStatus::Approved
            } else {
                SessionStatus::Pending
            },
            created_at: crate::server::now_iso(),
        };

        self.sessions
            .write()
            .unwrap()
            .insert(session.id.clone(), session.clone());
        self.publish();
        session
    }

    pub fn session(&self, id: &str) -> Option<Session> {
        self.sessions.read().unwrap().get(id).cloned()
    }

    pub fn session_is_approved(&self, id: &str) -> bool {
        self.sessions
            .read()
            .unwrap()
            .get(id)
            .map(|s| s.status == SessionStatus::Approved)
            .unwrap_or(false)
    }

    pub fn resolve_session(&self, id: &str, approve: bool, remember: bool) -> Result<()> {
        let device = {
            let mut sessions = self.sessions.write().unwrap();
            let Some(session) = sessions.get_mut(id) else {
                anyhow::bail!("That request is no longer waiting.");
            };
            session.status = if approve {
                SessionStatus::Approved
            } else {
                SessionStatus::Denied
            };
            (session.device_id.clone(), session.label.clone())
        };

        if approve && remember {
            let mut remembered = self.remembered.write().unwrap();
            if !remembered.iter().any(|d| d.device_id == device.0) {
                remembered.push(RememberedDevice {
                    device_id: device.0.clone(),
                    label: device.1,
                    remembered_at: crate::server::now_iso(),
                });
            }
            drop(remembered);
            self.persist_remembered()?;
        }

        // Approving one device approves every session it already has
        // waiting — a phone that retried twice shouldn't need two yeses.
        if approve {
            let mut sessions = self.sessions.write().unwrap();
            for session in sessions.values_mut() {
                if session.device_id == device.0 && session.status == SessionStatus::Pending {
                    session.status = SessionStatus::Approved;
                }
            }
        }

        self.publish();
        Ok(())
    }

    /// Revokes a remembered device and cuts off its live sessions, so
    /// "forget" takes effect now rather than at next connect.
    pub fn forget_device(&self, device_id: &str) -> Result<()> {
        self.remembered.write().unwrap().retain(|d| d.device_id != device_id);
        {
            let mut sessions = self.sessions.write().unwrap();
            for session in sessions.values_mut() {
                if session.device_id == device_id {
                    session.status = SessionStatus::Denied;
                }
            }
        }
        self.persist_remembered()?;
        self.publish();
        Ok(())
    }

    fn pending_sessions(&self) -> Vec<Session> {
        let mut list: Vec<_> = self
            .sessions
            .read()
            .unwrap()
            .values()
            .filter(|s| s.status == SessionStatus::Pending)
            .cloned()
            .collect();
        list.sort_by(|a, b| a.created_at.cmp(&b.created_at));
        list
    }

    fn persist_remembered(&self) -> Result<()> {
        write_json(
            &self.data_dir.join("remembered.json"),
            &*self.remembered.read().unwrap(),
        )
    }

    pub fn subscribe_mode(&self) -> tokio::sync::watch::Receiver<NetworkMode> {
        self.mode_tx.subscribe()
    }

    pub fn apply_settings(&self, patch: SettingsPatch) -> Result<()> {
        let mut new_mode = None;
        {
            let mut settings = self.settings.write().unwrap();
            if let Some(v) = patch.network_mode {
                if v != settings.network_mode {
                    settings.network_mode = v;
                    new_mode = Some(v);
                }
            }
            if let Some(v) = patch.shared_folder {
                settings.shared_folder = v;
            }
            if let Some(v) = patch.pin {
                settings.pin = v;
            }
            if let Some(v) = patch.https_port {
                settings.https_port = v;
            }
            if let Some(v) = patch.http_port {
                settings.http_port = v;
            }
            if let Some(v) = patch.theme {
                settings.theme = v;
            }
            if let Some(v) = patch.start_with_windows {
                settings.start_with_windows = v;
            }
            if let Some(v) = patch.require_pin_every_time {
                settings.require_pin_every_time = v;
            }
        }
        self.persist_settings()?;

        // Signalled after the write, so the listener that restarts reads
        // the new mode rather than racing the value it's restarting for.
        if let Some(mode) = new_mode {
            let _ = self.mode_tx.send(mode);
        }

        self.publish();
        Ok(())
    }

    pub fn regenerate_pin(&self) -> Result<String> {
        let pin = random_pin();
        self.settings.write().unwrap().pin = pin.clone();
        self.persist_settings()?;
        self.publish();
        Ok(pin)
    }

    pub fn add_transfer(&self, transfer: Transfer) {
        self.transfers.write().unwrap().push(transfer);
        self.publish();
    }

    /// Records progress, publishing at most a few times a second.
    ///
    /// The engine calls this every 64 KB. On a fast link that's hundreds
    /// of calls per second, and publishing each one would serialize a
    /// full snapshot and push it to every connected device — turning a
    /// file transfer into a denial of service against your own phone.
    /// Throttling here rather than at the call site keeps the rule in one
    /// place, where it can't be forgotten by a future caller.
    pub fn update_transfer_progress(&self, id: &str, done: u64, rate: Option<u64>) {
        let should_publish = {
            let mut transfers = self.transfers.write().unwrap();
            let Some(transfer) = transfers.iter_mut().find(|t| t.id == id) else {
                return;
            };
            transfer.bytes_done = done;
            transfer.rate = rate;

            let mut last = self.last_progress_publish.lock().unwrap();
            if last.elapsed() >= Duration::from_millis(250) {
                *last = Instant::now();
                true
            } else {
                false
            }
        };

        if should_publish {
            self.publish();
        }
    }

    /// Terminal states always publish — this is the update people are
    /// actually waiting on, and throttling it would leave a finished
    /// transfer looking stuck at 98%.
    pub fn finish_transfer(&self, id: &str, state: TransferState, error: Option<String>) {
        {
            let mut transfers = self.transfers.write().unwrap();
            if let Some(transfer) = transfers.iter_mut().find(|t| t.id == id) {
                if transfer.state != TransferState::Cancelled {
                    transfer.state = state;
                }
                transfer.rate = None;
                transfer.error = error;
                transfer.finished_at = Some(crate::server::now_iso());
                if state == TransferState::Done {
                    if transfer.bytes_total > 0 {
                        // Known size: snap to 100% so rounding can't leave
                        // a finished transfer sitting at 99%.
                        transfer.bytes_done = transfer.bytes_total;
                    } else {
                        // Size wasn't knowable up front (multipart carries
                        // no per-part length). Now that it's finished, the
                        // amount transferred *is* the total — without this
                        // the record would report zero bytes for a file
                        // that arrived perfectly well.
                        transfer.bytes_total = transfer.bytes_done;
                    }
                }
            }
        }
        self.publish();
    }

    pub fn clear_finished_transfers(&self) {
        self.transfers.write().unwrap().retain(|t| {
            matches!(t.state, TransferState::Active | TransferState::Queued)
        });
        self.publish();
    }

    pub fn cancel_transfer(&self, id: &str) {
        {
            let mut transfers = self.transfers.write().unwrap();
            if let Some(t) = transfers.iter_mut().find(|t| t.id == id) {
                t.state = TransferState::Cancelled;
                t.rate = None;
            }
        }
        self.publish();
    }

    /// Serialize once, hand the same string to everyone. A send error
    /// only means nobody is listening yet, which is normal at startup.
    pub fn publish(&self) {
        let _ = self.tx.send(self.snapshot_json());
    }

    // ---- persistence ------------------------------------------------

    fn persist_settings(&self) -> Result<()> {
        write_json(&self.data_dir.join("settings.json"), &*self.settings.read().unwrap())
    }

}

// ------------------------------------------------------------ helpers

/// Reads a JSON file, treating "absent" and "unreadable" differently.
///
/// A missing file is normal — first run. A file that exists but won't
/// parse is a problem, and the earlier version of this returned `None`
/// for both. That turned a schema mismatch into a silently empty device
/// list: every paired device vanished on restart with nothing logged.
/// Falling back to defaults is still the right behavior, but it must be
/// loud.
/// Stable per browser-on-a-device: same phone reconnecting keeps its
/// entry rather than piling up duplicates. Not a security boundary —
/// both inputs are client-controlled — purely a display key.
fn client_id(address: &str, agent: &str) -> String {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(format!("{address}|{agent}").as_bytes());
    digest.iter().take(8).map(|b| format!("{b:02x}")).collect()
}

fn iso(time: SystemTime) -> String {
    let secs = time
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    crate::server::iso_from_unix(secs)
}

fn read_json<T: serde::de::DeserializeOwned>(path: &Path) -> Option<T> {
    let text = std::fs::read_to_string(path).ok()?;

    // Strip a UTF-8 BOM if present. These files are plain JSON sitting in
    // a folder a person can open, and most Windows editors — Notepad and
    // PowerShell's own `Set-Content -Encoding utf8` among them — write
    // one. JSON parsers reject it, so without this a single hand-edit
    // silently empties the paired-device list.
    let text = text.strip_prefix('\u{feff}').unwrap_or(&text);

    match serde_json::from_str(text) {
        Ok(value) => Some(value),
        Err(err) => {
            tracing::error!(
                file = %path.display(),
                "could not read this file, falling back to defaults: {err}"
            );
            None
        }
    }
}

/// Writes through a temporary file, so a crash mid-write can't leave a
/// truncated settings file that reads as "no settings at all" and
/// silently resets the user's PIN and shared folder on next start.
fn write_json<T: serde::Serialize>(path: &Path, value: &T) -> Result<()> {
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(value)?)?;
    std::fs::rename(&tmp, path)?;
    Ok(())
}

fn platform_name() -> String {
    format!("{} {}", std::env::consts::OS, std::env::consts::ARCH)
}

/// Free and total bytes on whichever disk holds `path`.
///
/// Reports the disk the *shared folder* lives on, not the system drive:
/// on a machine sharing from a second drive, the system drive's figures
/// would be actively misleading when deciding whether a transfer fits.
/// Picks the longest matching mount point so `D:\` doesn't win over
/// `D:\data` when both are mounted.
fn disk_usage_for(path: &Path) -> (u64, u64) {
    let disks = sysinfo::Disks::new_with_refreshed_list();

    // Deliberately *not* canonicalized. On Windows `canonicalize` returns
    // a verbatim path (`\\?\C:\Users\…`), which never `starts_with` a
    // mount point of `C:\` — so every disk is filtered out and the whole
    // function silently reports zero.
    let target = normalize_for_compare(path);

    let best = disks
        .list()
        .iter()
        .filter(|disk| {
            target.starts_with(&normalize_for_compare(disk.mount_point()))
        })
        .max_by_key(|disk| disk.mount_point().as_os_str().len());

    match best {
        Some(disk) => (disk.available_space(), disk.total_space()),
        None => (0, 0),
    }
}

/// Lowercased, with any Windows verbatim prefix removed, so two paths
/// naming the same location compare equal. Windows paths are
/// case-insensitive, and `Path::starts_with` is not.
fn normalize_for_compare(path: &Path) -> String {
    path.to_string_lossy()
        .trim_start_matches(r"\\?\")
        .to_lowercase()
        .replace('/', "\\")
}

/// Returns (lan, tailscale). A Tailscale address lives in 100.64.0.0/10,
/// which is how it's told apart from an ordinary LAN address without
/// asking Tailscale itself.
fn detect_addresses() -> (Option<IpAddr>, Option<IpAddr>) {
    // `local_ip()` resolves the interface that carries outbound traffic,
    // i.e. the one holding the default route. Enumerating interfaces and
    // taking the first private address instead picks whatever the OS
    // happens to list first — frequently a VPN, Hyper-V, WSL or Docker
    // adapter, whose address a phone on your Wi-Fi cannot reach. The
    // displayed address has to be one that actually works, since people
    // type it into another device.
    let lan = match local_ip_address::local_ip() {
        Ok(ip @ IpAddr::V4(v4)) if !v4.is_loopback() => Some(ip),
        _ => None,
    };

    // Tailscale still needs the interface list: it isn't the default
    // route, and it's identifiable by its 100.64.0.0/10 range.
    let mut tailscale = None;
    if let Ok(interfaces) = local_ip_address::list_afinet_netifas() {
        for (_name, ip) in interfaces {
            let IpAddr::V4(v4) = ip else { continue };
            let octets = v4.octets();
            if octets[0] == 100 && (64..128).contains(&octets[1]) {
                tailscale = Some(ip);
                break;
            }
        }
    }

    (lan, tailscale)
}
