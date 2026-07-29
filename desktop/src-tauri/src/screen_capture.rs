/// Screen capture module for Computer Use.
/// Captures the primary display and returns a base64-encoded PNG.
/// Only captures when an active Computer Use session is running
/// and the user has granted screen capture permission.
use anyhow::{anyhow, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::AppHandle;

/// Global flag: true while a Computer Use screen-capture session is active.
/// Default OFF — capture is impossible until the user explicitly activates it.
static COMPUTER_USE_ACTIVE: AtomicBool = AtomicBool::new(false);

/// Unix seconds of the last activation/capture — drives the inactivity timeout.
static LAST_ACTIVITY_AT: AtomicU64 = AtomicU64::new(0);

/// Auto-stop a capture session after this many seconds without a capture, so a
/// session can never be left running silently (e.g. after a caller crash).
const INACTIVITY_TIMEOUT_SECS: u64 = 120;

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Activate a Computer Use screen-capture session. MUST be called only after an
/// explicit user action consenting to screen capture. Registered as a command so
/// the UI drives the consent flow. Default state is OFF.
#[tauri::command]
pub fn activate_computer_use() -> bool {
    LAST_ACTIVITY_AT.store(now_secs(), Ordering::SeqCst);
    COMPUTER_USE_ACTIVE.store(true, Ordering::SeqCst);
    true
}

/// Deactivate the session (explicit stop). Idempotent.
#[tauri::command]
pub fn deactivate_computer_use() {
    COMPUTER_USE_ACTIVE.store(false, Ordering::SeqCst);
}

/// Whether a capture session is currently active (for a UI indicator).
#[tauri::command]
pub fn computer_use_active() -> bool {
    COMPUTER_USE_ACTIVE.load(Ordering::SeqCst)
}

/// Capture the primary display and return as base64 PNG.
/// Fails unless a session is active AND has not hit the inactivity timeout.
#[tauri::command]
pub fn capture_screen() -> Result<String, String> {
    if !COMPUTER_USE_ACTIVE.load(Ordering::SeqCst) {
        return Err(
            "Screen capture is only allowed during an active Computer Use session".to_string(),
        );
    }
    // Inactivity timeout: auto-stop a stale session rather than keep capturing.
    let last = LAST_ACTIVITY_AT.load(Ordering::SeqCst);
    if now_secs().saturating_sub(last) > INACTIVITY_TIMEOUT_SECS {
        deactivate_computer_use();
        return Err("Computer Use session timed out; re-activate to continue".to_string());
    }
    LAST_ACTIVITY_AT.store(now_secs(), Ordering::SeqCst);
    _capture_and_encode().map_err(|e| e.to_string())
}

fn _capture_and_encode() -> Result<String> {
    #[cfg(target_os = "windows")]
    {
        capture_windows()
    }
    #[cfg(target_os = "macos")]
    {
        capture_macos()
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        Err(anyhow!("Screen capture not supported on this platform"))
    }
}

// ─── Windows capture ──────────────────────────────────────────────────────────

#[cfg(target_os = "windows")]
fn capture_windows() -> Result<String> {
    use std::process::Command;

    // Use PowerShell to capture the screen as PNG bytes.
    // This is the simplest cross-version approach on Windows without extra deps.
    let output = Command::new("powershell")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            r#"
Add-Type -AssemblyName System.Windows.Forms;
Add-Type -AssemblyName System.Drawing;
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds;
$bitmap = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height);
$graphics = [System.Drawing.Graphics]::FromImage($bitmap);
$graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size);
$ms = New-Object System.IO.MemoryStream;
$bitmap.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png);
[Convert]::ToBase64String($ms.ToArray())
"#,
        ])
        .output()
        .map_err(|e| anyhow!("PowerShell screen capture failed: {}", e))?;

    if !output.status.success() {
        let err = String::from_utf8_lossy(&output.stderr);
        return Err(anyhow!("Screen capture error: {}", err));
    }

    let b64 = String::from_utf8(output.stdout)
        .map_err(|e| anyhow!("UTF-8 decode error: {}", e))?
        .trim()
        .to_string();

    Ok(b64)
}

// ─── macOS capture ─────────────────────────────────────────────────────────────

#[cfg(target_os = "macos")]
fn capture_macos() -> Result<String> {
    use std::fs;
    use std::process::Command;

    // screencapture writes to a temp file, then we read + encode it
    let tmp_path = "/tmp/secretaryai_capture.png";

    let status = Command::new("screencapture")
        .args(["-x", "-m", tmp_path]) // -x: no sound, -m: main display only
        .status()
        .map_err(|e| anyhow!("screencapture failed: {}", e))?;

    if !status.success() {
        return Err(anyhow!("screencapture exited with status: {}", status));
    }

    let bytes = fs::read(tmp_path).map_err(|e| anyhow!("Failed to read capture file: {}", e))?;

    let _ = fs::remove_file(tmp_path); // clean up

    Ok(STANDARD.encode(&bytes))
}
