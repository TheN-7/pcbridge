//! Wire types shared with the interface.
//!
//! These mirror `src/lib/state/bridge.svelte.ts` exactly. Everything is
//! camelCase on the wire so the TypeScript side needs no translation
//! layer — if a field is renamed here, it must be renamed there, and
//! the compiler on neither side will catch it for you. Keeping the two
//! files adjacent in review is the safeguard.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum TransferState {
    Queued,
    Active,
    Done,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum TransferDirection {
    Upload,
    Download,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Transfer {
    pub id: String,
    pub name: String,
    pub device_id: Option<String>,
    pub device_name: String,
    pub direction: TransferDirection,
    pub state: TransferState,
    pub bytes_done: u64,
    pub bytes_total: u64,
    /// Bytes per second while active, `None` once it isn't.
    pub rate: Option<u64>,
    pub started_at: String,
    pub finished_at: Option<String>,
    pub error: Option<String>,
}

/// How the network-facing listener is exposed.
///
/// Exactly one is active at a time — they share the same port, so running
/// both would be ambiguous for anyone typing an address. The loopback API
/// this window uses is unaffected either way.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Default)]
#[serde(rename_all = "lowercase")]
pub enum NetworkMode {
    /// Encrypted. Browsers show a one-time certificate warning because no
    /// public authority can vouch for a private address.
    #[default]
    Https,
    /// No warning, and no encryption: the PIN and every file cross the
    /// network in the clear. Only sane on a network you fully trust.
    Http,
}

impl NetworkMode {
    pub fn scheme(self) -> &'static str {
        match self {
            NetworkMode::Https => "https",
            NetworkMode::Http => "http",
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Default)]
#[serde(rename_all = "lowercase")]
pub enum Theme {
    #[default]
    System,
    Dark,
    Light,
}

/// Everything the user can change. This is the part that persists to
/// disk, and the part "one source, many windows" is really about.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Settings {
    pub shared_folder: String,
    pub pin: String,
    /// How the network-facing listener is exposed. Switchable at runtime
    /// from Overview; the listener restarts in place.
    #[serde(default)]
    pub network_mode: NetworkMode,
    pub https_port: u16,
    pub http_port: u16,
    pub theme: Theme,
    pub start_with_windows: bool,
    pub require_pin_every_time: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            // A dedicated folder, never the home directory.
            //
            // Sharing `~` means anyone holding the PIN can read AppData,
            // NTUSER.DAT, SSH keys, and every dotfile containing an API
            // token. The old version defaulted that way and relied on
            // people noticing and narrowing it; almost nobody does.
            // Opting in to more is a deliberate act in Settings; opting
            // out of everything shouldn't have to be.
            shared_folder: default_share_dir(),
            network_mode: NetworkMode::Https,
            pin: random_pin(),
            https_port: 8000,
            http_port: 8001,
            theme: Theme::System,
            start_with_windows: false,
            require_pin_every_time: false,
        }
    }
}

/// A patch from the interface. Every field optional so a screen can send
/// only what it changed, which is what lets Overview and Settings edit
/// overlapping subsets of the same object without clobbering each other.
#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SettingsPatch {
    pub shared_folder: Option<String>,
    pub pin: Option<String>,
    pub network_mode: Option<NetworkMode>,
    pub https_port: Option<u16>,
    pub http_port: Option<u16>,
    pub theme: Option<Theme>,
    pub start_with_windows: Option<bool>,
    pub require_pin_every_time: Option<bool>,
}

/// A browser currently talking to this PC.
///
/// Not persisted and not paired — this is a live observation, not a
/// relationship. A device appears when it makes a request and disappears
/// once it's been quiet long enough, which is what makes the Devices
/// screen answer "who can see my files right now".
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConnectedClient {
    pub id: String,
    /// Best guess from the User-Agent, e.g. "Chrome on Android".
    pub label: String,
    pub address: String,
    pub connected_at: String,
    pub last_seen: String,
    /// Open event streams. Greater than zero means the app is on screen
    /// right now, rather than merely having been used recently.
    pub streams: u32,
}

/// Turns a User-Agent into something a person recognizes.
///
/// Deliberately coarse. The goal is "is that my phone or my laptop?",
/// not analytics, and User-Agent strings lie too often to justify
/// anything more elaborate.
pub fn describe_agent(agent: &str) -> String {
    let ua = agent.to_ascii_lowercase();

    let os = if ua.contains("android") {
        "Android"
    } else if ua.contains("iphone") {
        "iPhone"
    } else if ua.contains("ipad") {
        "iPad"
    } else if ua.contains("windows") {
        "Windows"
    } else if ua.contains("mac os") || ua.contains("macintosh") {
        "Mac"
    } else if ua.contains("linux") {
        "Linux"
    } else {
        "Unknown device"
    };

    // Order matters: Edge and Chrome both claim Safari, and Edge also
    // claims Chrome. Checking most-specific first avoids labelling an
    // Edge browser as Safari.
    let browser = if ua.contains("edg/") {
        Some("Edge")
    } else if ua.contains("firefox") || ua.contains("fxios") {
        Some("Firefox")
    } else if ua.contains("chrome") || ua.contains("crios") {
        Some("Chrome")
    } else if ua.contains("safari") {
        Some("Safari")
    } else {
        None
    };

    match browser {
        Some(b) => format!("{b} on {os}"),
        None => os.to_string(),
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum SessionStatus {
    /// Correct PIN, waiting for someone at the PC to allow it.
    Pending,
    Approved,
    Denied,
}

/// A browser that has entered the right PIN.
///
/// The PIN alone is deliberately not enough to reach any file: it can be
/// read over a shoulder or forwarded to someone else, and it never
/// changes on its own. Requiring a person at the PC to allow each new
/// device turns "knows the PIN" into "knows the PIN *and* someone here
/// said yes".
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Session {
    pub id: String,
    /// Derived from the device's key, so "remember" recognises the same
    /// device on a later visit regardless of what address it turns up on.
    pub device_id: String,
    /// Hash of the key this device presented (or was just issued). Kept
    /// so `resolve_session` can record it when the user chooses to
    /// remember, and `#[serde(skip)]` so it never leaves the process —
    /// it is the stored half of a credential, not something to render.
    #[serde(skip)]
    pub key_hash: String,
    pub label: String,
    pub address: String,
    pub status: SessionStatus,
    pub created_at: String,
}

/// Devices the user chose to trust permanently. Persisted; the sessions
/// themselves are not, so a restart re-asks for anything not remembered.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct RememberedDevice {
    pub device_id: String,
    /// SHA-256 of the key this device must present to be recognised.
    ///
    /// Only the hash is stored, so a copy of remembered.json is not
    /// itself a way in — the same reason a password file holds hashes.
    ///
    /// `default` because entries written before device keys existed have
    /// no such field. They deserialize to an empty string, which no
    /// presented key can ever hash to, so those devices are asked for
    /// approval once more. That is the intended migration: the trust they
    /// were granted rested on an address and a User-Agent, neither of
    /// which is a secret, so it should not carry over silently.
    #[serde(default)]
    pub key_hash: String,
    pub label: String,
    pub remembered_at: String,
}

/// Facts about this machine. Derived at runtime, never persisted.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ServerInfo {
    pub hostname: String,
    pub platform: String,
    pub lan_address: Option<String>,
    pub tailscale_address: Option<String>,
    pub fingerprint: String,
    pub storage_free: u64,
    pub storage_total: u64,
}

/// One complete picture of the application, pushed to every connected
/// client whenever anything changes.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Snapshot {
    pub serving: bool,
    pub settings: Settings,
    pub server: ServerInfo,
    pub transfers: Vec<Transfer>,
    /// Browsers connected right now. See `ConnectedClient`.
    pub clients: Vec<ConnectedClient>,
    /// Devices waiting to be allowed in. Drives the approval prompt.
    pub pending_sessions: Vec<Session>,
    pub remembered_devices: Vec<RememberedDevice>,
}

// ------------------------------------------------------------ helpers

pub fn random_pin() -> String {
    format!("{:06}", rand::random::<u32>() % 1_000_000)
}

pub fn random_id() -> String {
    format!("{:016x}", rand::random::<u64>())
}

fn dirs_home() -> String {
    std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_else(|_| ".".to_string())
}

/// `~/PC Bridge` — created on first run so the folder exists before
/// anyone tries to browse it, and is somewhere obvious enough that
/// dragging files into it is the natural thing to do.
pub fn default_share_dir() -> String {
    std::path::Path::new(&dirs_home())
        .join("PC Bridge")
        .to_string_lossy()
        .to_string()
}
