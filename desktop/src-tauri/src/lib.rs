mod credentials;
mod db;
mod heartbeat;
mod screen_capture;
mod sync;
mod tray;
mod watcher;

use log::{info, warn, error};
use std::{
    fs,
    io::{Read, Write},
    path::PathBuf,
};
use tauri::{Manager, WindowEvent};

// ── Lock file helpers ─────────────────────────────────────────────────────────

fn lock_file_path() -> PathBuf {
    let mut path = dirs_next();
    path.push("SecretaryAI");
    path.push("agent.lock");
    path
}

/// Platform data dir (AppData/Roaming on Windows, ~/Library/Application Support on macOS, ~/.local/share on Linux)
fn dirs_next() -> PathBuf {
    #[cfg(target_os = "windows")]
    {
        std::env::var("APPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("."))
    }
    #[cfg(target_os = "macos")]
    {
        let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
        PathBuf::from(home)
            .join("Library")
            .join("Application Support")
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
        PathBuf::from(home).join(".local").join("share")
    }
}

fn is_pid_alive(pid: u32) -> bool {
    #[cfg(unix)]
    {
        // kill(pid, 0) returns 0 if process exists
        unsafe { libc::kill(pid as i32, 0) == 0 }
    }
    #[cfg(windows)]
    {
        use std::ptr;
        unsafe {
            let handle = winapi::um::processthreadsapi::OpenProcess(
                winapi::um::winnt::PROCESS_QUERY_LIMITED_INFORMATION,
                0,
                pid,
            );
            if handle == ptr::null_mut() {
                return false;
            }
            let mut exit_code: u32 = 0;
            let alive = winapi::um::processthreadsapi::GetExitCodeProcess(handle, &mut exit_code) != 0
                && exit_code == winapi::um::minwinbase::STILL_ACTIVE;
            winapi::um::handleapi::CloseHandle(handle);
            alive
        }
    }
    #[cfg(not(any(unix, windows)))]
    {
        // Assume alive if we can't check
        let _ = pid;
        true
    }
}

/// Returns false if another live instance already holds the lock.
fn acquire_lock() -> bool {
    let lock_path = lock_file_path();

    if let Some(parent) = lock_path.parent() {
        let _ = fs::create_dir_all(parent);
    }

    if lock_path.exists() {
        // Read existing PID
        let mut contents = String::new();
        if let Ok(mut f) = fs::File::open(&lock_path) {
            let _ = f.read_to_string(&mut contents);
        }
        if let Ok(existing_pid) = contents.trim().parse::<u32>() {
            if is_pid_alive(existing_pid) {
                warn!("Lock file held by live PID {}; another instance running", existing_pid);
                return false;
            }
            info!("Stale lock file from PID {} (crashed); taking over", existing_pid);
        }
        let _ = fs::remove_file(&lock_path);
    }

    // Write our PID
    let our_pid = std::process::id();
    match fs::File::create(&lock_path) {
        Ok(mut f) => {
            let _ = write!(f, "{}", our_pid);
            info!("Lock acquired by PID {}", our_pid);
            true
        }
        Err(e) => {
            warn!("Could not create lock file: {}", e);
            true // Don't block startup if we can't write the lock
        }
    }
}

fn release_lock() {
    let _ = fs::remove_file(lock_file_path());
    info!("Lock file released");
}

// ── App entry point ───────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Single-instance enforcement with crash recovery
    if !acquire_lock() {
        eprintln!("Another instance of SecretaryAI is already running. Exiting.");
        std::process::exit(0);
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec!["--hidden"]),
        ))
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            credentials::store_credential,
            credentials::get_credential,
            credentials::delete_credential,
            sync::trigger_sync,
            sync::get_sync_status,
            db::query_local,
            screen_capture::capture_screen,
        ])
        .setup(|app| {
            // Initialize local encrypted database
            db::init_local_db(app.handle())?;

            // Set up system tray
            tray::setup_tray(app)?;

            // Start background sync loop
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                sync::start_sync_loop(handle).await;
            });

            // Start file watcher for depletion reports
            let handle2 = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                watcher::start_file_watcher(handle2).await;
            });

            // Check for OTA updates in the background
            {
                use tauri_plugin_updater::UpdaterExt;
                let update_handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    match update_handle.updater() {
                        Ok(updater) => match updater.check().await {
                            Ok(Some(update)) => {
                                info!("Update available: {}", update.version);
                                if let Err(e) = update
                                    .download_and_install(|_, _| {}, || {
                                        info!("Update downloaded — will apply on next launch");
                                    })
                                    .await
                                {
                                    error!("Update install failed: {e}");
                                }
                            }
                            Ok(None) => info!("SecretaryAI is up to date"),
                            Err(e) => warn!("Update check failed: {e}"),
                        },
                        Err(e) => warn!("Updater unavailable: {e}"),
                    }
                });
            }

            // Start cloud heartbeat
            // Read config from the store; fall back to env vars
            let api_url = std::env::var("SECRETARY_API_URL")
                .unwrap_or_else(|_| "https://api.secretaryai.com".to_string());
            let company_id = std::env::var("SECRETARY_COMPANY_ID").unwrap_or_default();
            let version = env!("CARGO_PKG_VERSION").to_string();
            tauri::async_runtime::spawn(async move {
                heartbeat::start_heartbeat(api_url, company_id, version).await;
            });

            Ok(())
        })
        .on_window_event(|_window, event| {
            // Minimize to tray instead of closing (window close never exits the process)
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                #[cfg(not(target_os = "macos"))]
                let _ = _window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building SecretaryAI desktop")
        .run(|_app, event| {
            if let tauri::RunEvent::Exit = event {
                release_lock();
            }
        });
}
