//! Launching the bundled QuickBooks Desktop connector.
//!
//! QuickBooks Desktop has no cloud API: a local agent has to run on the same
//! Windows machine as the company file and bridge it to the cloud. That agent
//! is `connector/` in this repository, packaged into a single executable by
//! `connector/connector.spec` and bundled into the Windows installer as a Tauri
//! resource (see `.github/workflows/release-desktop.yml`).
//!
//! Before this module existed the connector shipped to nobody: it was a Python
//! package in the repository, and the release notes told customers it was
//! "included in the installer" when nothing bundled it. This is what makes that
//! claim true.
//!
//! The connector is only bundled for Windows, because QuickBooks Desktop only
//! runs there. On macOS and Linux every function here reports `bundled: false`
//! and explains why rather than failing opaquely.

use std::path::PathBuf;
use std::process::Child;
use std::sync::{Mutex, OnceLock};

use serde::Serialize;
use tauri::{AppHandle, Manager};

/// Path of the connector inside the bundle, relative to the resource directory.
#[cfg(target_os = "windows")]
const CONNECTOR_RESOURCE: &str = "resources/SecretaryAIConnector.exe";
#[cfg(not(target_os = "windows"))]
const CONNECTOR_RESOURCE: &str = "resources/SecretaryAIConnector";

/// The child process, when we started one. Tauri commands can run concurrently,
/// so this is behind a mutex.
fn child_slot() -> &'static Mutex<Option<Child>> {
    static SLOT: OnceLock<Mutex<Option<Child>>> = OnceLock::new();
    SLOT.get_or_init(|| Mutex::new(None))
}

#[derive(Serialize)]
pub struct ConnectorStatus {
    /// Whether a connector executable is present in this build.
    pub bundled: bool,
    /// Whether this app started it and the process is still alive.
    pub running: bool,
    /// Whether the connector has already registered with the cloud.
    pub registered: bool,
    /// Where the connector writes its log, for support.
    pub log_path: Option<String>,
    /// Human-readable explanation when `bundled` is false.
    pub detail: Option<String>,
}

/// Per-user data directory the connector uses for its state and log file.
/// Must match `default_state_dir()` in `connector/config.py`.
fn connector_data_dir() -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        dirs_next::data_dir().map(|d| d.join("SecretaryAI"))
    }
    #[cfg(target_os = "macos")]
    {
        dirs_next::home_dir().map(|h| h.join("Library/Application Support/SecretaryAI"))
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        dirs_next::data_dir().map(|d| d.join("SecretaryAI"))
    }
}

fn connector_path(app: &AppHandle) -> Option<PathBuf> {
    let p = app
        .path()
        .resolve(CONNECTOR_RESOURCE, tauri::path::BaseDirectory::Resource)
        .ok()?;
    if p.is_file() {
        Some(p)
    } else {
        None
    }
}

#[tauri::command]
pub fn connector_status(app: AppHandle) -> ConnectorStatus {
    let data_dir = connector_data_dir();
    let registered = data_dir
        .as_ref()
        .map(|d| d.join("connector_state.json").is_file())
        .unwrap_or(false);
    let log_path = data_dir
        .as_ref()
        .map(|d| d.join("connector.log").to_string_lossy().into_owned());

    // `try_wait` reaps the child and tells us whether it is still running.
    let running = {
        let mut slot = child_slot().lock().expect("connector mutex poisoned");
        match slot.as_mut() {
            Some(child) => match child.try_wait() {
                Ok(Some(_status)) => {
                    *slot = None; // exited
                    false
                }
                Ok(None) => true,
                Err(_) => false,
            },
            None => false,
        }
    };

    let bundled = connector_path(&app).is_some();
    let detail = if bundled {
        None
    } else if cfg!(target_os = "windows") {
        Some(
            "The QuickBooks Desktop connector is missing from this installation. \
             Reinstall SecretaryAI, or contact support."
                .to_string(),
        )
    } else {
        Some(
            "QuickBooks Desktop runs only on Windows, so the connector is not \
             included in this build. Use QuickBooks Online here instead."
                .to_string(),
        )
    };

    ConnectorStatus {
        bundled,
        running,
        registered,
        log_path,
        detail,
    }
}

/// Start the connector.
///
/// `install_secret` is only needed for the very first registration; afterwards
/// the connector authenticates with the token it persisted, so the caller may
/// pass an empty string.
#[tauri::command]
pub fn start_connector(
    app: AppHandle,
    cloud_url: String,
    company_id: String,
    install_secret: String,
) -> Result<(), String> {
    if cloud_url.is_empty() || company_id.is_empty() {
        return Err("cloud_url and company_id are both required".to_string());
    }

    let mut slot = child_slot().lock().map_err(|e| e.to_string())?;

    // Already running? Don't start a second one: two connectors polling the
    // same company would execute every task twice.
    if let Some(child) = slot.as_mut() {
        match child.try_wait() {
            Ok(None) => return Ok(()),
            _ => *slot = None,
        }
    }

    let exe = connector_path(&app).ok_or_else(|| {
        if cfg!(target_os = "windows") {
            "The connector executable is missing from this installation.".to_string()
        } else {
            "QuickBooks Desktop is Windows-only; no connector is bundled in this build.".to_string()
        }
    })?;

    let mut cmd = std::process::Command::new(&exe);
    cmd.env("SECRETARY_CLOUD_URL", &cloud_url)
        .env("SECRETARY_COMPANY_ID", &company_id);
    if !install_secret.is_empty() {
        cmd.env("SECRETARY_INSTALL_SECRET", &install_secret);
    }

    // Don't flash a console window on Windows — the connector logs to a file in
    // per-user appdata, which is what support actually reads.
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let child = cmd
        .spawn()
        .map_err(|e| format!("Could not start the connector ({}): {e}", exe.display()))?;

    log::info!("connector started: {}", exe.display());
    *slot = Some(child);
    Ok(())
}

#[tauri::command]
pub fn stop_connector() -> Result<(), String> {
    let mut slot = child_slot().lock().map_err(|e| e.to_string())?;
    if let Some(mut child) = slot.take() {
        child.kill().map_err(|e| e.to_string())?;
        let _ = child.wait();
        log::info!("connector stopped");
    }
    Ok(())
}

/// Last `lines` lines of the connector log, for the support/diagnostics screen.
#[tauri::command]
pub fn connector_log_tail(lines: usize) -> Result<String, String> {
    let path = connector_data_dir()
        .map(|d| d.join("connector.log"))
        .ok_or_else(|| "Could not determine the application data directory".to_string())?;
    if !path.is_file() {
        return Ok(String::new());
    }
    let text = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let n = lines.clamp(1, 2000);
    let kept: Vec<&str> = text.lines().rev().take(n).collect();
    Ok(kept.into_iter().rev().collect::<Vec<_>>().join("\n"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn data_dir_is_resolvable_and_namespaced() {
        let d = connector_data_dir().expect("a data directory should resolve");
        assert!(
            d.ends_with("SecretaryAI"),
            "connector state must live under a SecretaryAI directory, got {}",
            d.display()
        );
    }

    #[test]
    fn log_tail_is_empty_when_no_log_exists_rather_than_erroring() {
        // A fresh install has no log yet; the diagnostics screen must not error.
        let out = connector_log_tail(50);
        assert!(out.is_ok(), "expected Ok, got {out:?}");
    }

    #[test]
    fn nothing_is_running_before_anything_is_started() {
        let slot = child_slot().lock().unwrap();
        assert!(slot.is_none());
    }
}
