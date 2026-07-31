mod credentials;
mod db;
mod heartbeat;
mod qb_detect;
mod screen_capture;
mod sync;
mod tray;
mod watcher;

use log::{error, info, warn};
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
        // kill(pid, 0) returns 0 if process exists (does not send a signal)
        unsafe { libc::kill(pid as libc::pid_t, 0) == 0 }
    }
    #[cfg(windows)]
    {
        use windows::Win32::Foundation::CloseHandle;
        use windows::Win32::System::Threading::{
            GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
        };
        // STILL_ACTIVE == 259 == STATUS_PENDING — means process has not exited
        const STILL_ACTIVE: u32 = 259;
        unsafe {
            match OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) {
                Ok(handle) => {
                    let mut exit_code: u32 = 0;
                    let alive = GetExitCodeProcess(handle, &mut exit_code).is_ok()
                        && exit_code == STILL_ACTIVE;
                    let _ = CloseHandle(handle);
                    alive
                }
                Err(_) => false,
            }
        }
    }
    #[cfg(not(any(unix, windows)))]
    {
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
                warn!(
                    "Lock file held by live PID {}; another instance running",
                    existing_pid
                );
                return false;
            }
            info!(
                "Stale lock file from PID {} (crashed); taking over",
                existing_pid
            );
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

// ── Startup breadcrumb log ────────────────────────────────────────────────────
// A release build is compiled with `panic = "abort"`, so any panic (or a native
// abort deep inside Tauri's `.build()`) terminates the process before a window
// ever appears — and on macOS the panic text is easy to miss in the OS crash
// report. To make startup failures diagnosable, we append timestamped
// breadcrumbs to `<app-data>/SecretaryAI/startup.log` and register a panic hook
// that records the panic message + location to that same file. The last line in
// the file tells us exactly how far startup got.

fn startup_log_path() -> PathBuf {
    let mut dir = dirs_next();
    dir.push("SecretaryAI");
    let _ = fs::create_dir_all(&dir);
    dir.push("startup.log");
    dir
}

fn startup_log(msg: &str) {
    let line = format!("[{}] {}", chrono::Local::now().to_rfc3339(), msg);
    // Always mirror to stderr for `Terminal` runs.
    eprintln!("[startup] {msg}");
    if let Ok(mut f) = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(startup_log_path())
    {
        let _ = writeln!(f, "{line}");
    }
}

fn install_panic_logger() {
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        startup_log(&format!("PANIC: {info}"));
        default_hook(info);
    }));
}

// ── App entry point ───────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Capture any panic (message + location) to the startup log before the
    // `panic = "abort"` runtime tears the process down.
    install_panic_logger();
    startup_log(&format!(
        "=== launch: v{} pid {} ===",
        env!("CARGO_PKG_VERSION"),
        std::process::id()
    ));

    // Single-instance enforcement with crash recovery
    if !acquire_lock() {
        startup_log("another instance already running — exiting");
        std::process::exit(0);
    }
    startup_log("lock acquired; building app");

    let app = tauri::Builder::default()
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
            screen_capture::activate_computer_use,
            screen_capture::deactivate_computer_use,
            screen_capture::computer_use_active,
            qb_detect::check_qb_installed,
        ])
        .setup(|app| {
            startup_log("setup: begin");

            // Initialize local encrypted database.
            // Non-fatal: a DB failure must never prevent the window from opening.
            match db::init_local_db(app.handle()) {
                Ok(()) => startup_log("setup: db ok"),
                Err(e) => {
                    error!("Local DB init failed (continuing): {e}");
                    startup_log(&format!("setup: db FAILED (continuing): {e}"));
                }
            }

            // Set up system tray. Non-fatal as well.
            match tray::setup_tray(app) {
                Ok(()) => startup_log("setup: tray ok"),
                Err(e) => {
                    error!("Tray setup failed (continuing): {e}");
                    startup_log(&format!("setup: tray FAILED (continuing): {e}"));
                }
            }

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
                                    .download_and_install(
                                        |_, _| {},
                                        || {
                                            info!("Update downloaded — will apply on next launch");
                                        },
                                    )
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
            let api_url = std::env::var("SECRETARY_API_URL").unwrap_or_else(|_| {
                if cfg!(debug_assertions) {
                    "http://localhost:8000".to_string()
                } else {
                    "https://api.secretaryai.com".to_string()
                }
            });
            let company_id = std::env::var("SECRETARY_COMPANY_ID").unwrap_or_default();
            let version = env!("CARGO_PKG_VERSION").to_string();
            tauri::async_runtime::spawn(async move {
                heartbeat::start_heartbeat(api_url, company_id, version).await;
            });

            startup_log("setup: done");
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
        .build(tauri::generate_context!());

    // Don't `.expect()` here: a hard panic in a `panic = "abort"` build turns a
    // recoverable "couldn't build" into an instant SIGABRT with no useful text.
    // Log the concrete error and exit cleanly instead.
    let app = match app {
        Ok(app) => {
            startup_log("build: ok — entering run loop");
            app
        }
        Err(e) => {
            startup_log(&format!("build: FAILED: {e}"));
            eprintln!("SecretaryAI failed to start: {e}");
            release_lock();
            std::process::exit(1);
        }
    };

    app.run(|_app, event| {
        if let tauri::RunEvent::Exit = event {
            release_lock();
        }
    });
}
