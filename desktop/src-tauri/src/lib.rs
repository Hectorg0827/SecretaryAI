mod credentials;
mod db;
mod screen_capture;
mod sync;
mod tray;
mod watcher;

use tauri::{Manager, WindowEvent};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
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

            Ok(())
        })
        .on_window_event(|_window, event| {
            // Minimize to tray instead of closing
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                #[cfg(not(target_os = "macos"))]
                _window.hide().unwrap();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running SecretaryAI desktop");
}
