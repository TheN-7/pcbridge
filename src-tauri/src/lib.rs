mod files;
mod model;
mod server;
mod store;
mod tls;

use std::error::Error;

use tauri::Manager;

use store::SharedState;

/// Where the desktop window should send its API calls.
///
/// Phones and browsers are served the interface by this same server, so
/// their API is same-origin and they never call this. Only the packaged
/// window needs telling, because it loads from the asset protocol.
#[tauri::command]
fn api_base(state: tauri::State<SharedState>) -> String {
    format!("http://127.0.0.1:{}", state.settings().http_port)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // rustls needs a crypto provider chosen before any TLS happens.
    // Installing it explicitly here, rather than relying on a single
    // enabled feature to auto-select one, means adding a dependency that
    // enables a second provider can't turn this into a runtime panic on
    // the first connection.
    let _ = rustls::crypto::ring::default_provider().install_default();

    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "pcbridge=info,tower_http=warn".into()),
        )
        .init();

    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init());

    // Autostart is a desktop-only idea — Android has no equivalent, and
    // the plugin isn't built for it, so this must not be registered
    // unconditionally or the mobile target stops compiling.
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ));
    }

    builder
        .setup(|app| {
            // Settings, paired devices, and the TLS identity all live
            // together in the per-user app data directory. Notably *not*
            // next to the executable: an installed copy under Program
            // Files wouldn't be writable, which is exactly how the
            // previous version lost people's PINs.
            let data_dir = app.path().app_data_dir().map_err(boxed)?;
            std::fs::create_dir_all(&data_dir)?;

            let identity = tls::ensure_identity(&data_dir).map_err(boxed)?;
            tracing::info!("certificate fingerprint {}", identity.fingerprint);

            let cert = identity.cert_pem.clone();
            let key = identity.key_pem.clone();

            let state = store::AppState::load(data_dir, identity).map_err(boxed)?;
            let settings = state.settings();

            app.manage(state.clone());

            // Loopback API for this window. A bound port must not take
            // the window down with it — the user needs a running
            // interface to go change the port in Settings.
            let local = state.clone();
            tauri::async_runtime::spawn(async move {
                if let Err(err) = server::serve_http(local, settings.http_port).await {
                    tracing::error!("local api stopped: {err}");
                }
            });

            // The listener phones connect to. Separate task so a TLS
            // failure leaves the desktop app fully usable rather than
            // bringing everything down.
            let devices = state.clone();
            tauri::async_runtime::spawn(async move {
                if let Err(err) =
                    server::serve_https(devices, settings.https_port, cert, key).await
                {
                    tracing::error!("device api stopped: {err}");
                }
            });

            // Drops browsers that have gone quiet. A closed tab is caught
            // immediately by the stream guard; this covers the cases with
            // no clean disconnect — a phone that slept, or Wi-Fi that
            // dropped mid-request.
            let pruner = state.clone();
            tauri::async_runtime::spawn(async move {
                let mut tick =
                    tokio::time::interval(std::time::Duration::from_secs(15));
                loop {
                    tick.tick().await;
                    if pruner.prune_clients() {
                        pruner.publish();
                    }
                }
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![api_base])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

fn boxed(err: impl std::fmt::Display) -> Box<dyn Error> {
    err.to_string().into()
}
