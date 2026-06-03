/// Detect whether QuickBooks Desktop is installed on this machine.
///
/// Windows: checks the registry key Intuit writes during QB installation.
/// macOS:   checks for the QuickBooks.app bundle in /Applications.
/// Linux:   always returns false (QB Desktop is Windows/macOS only).

#[tauri::command]
pub fn check_qb_installed() -> bool {
    detect_qb()
}

#[cfg(target_os = "windows")]
fn detect_qb() -> bool {
    use windows::Win32::System::Registry::{
        RegOpenKeyExW, RegCloseKey, HKEY, HKEY_LOCAL_MACHINE, KEY_READ,
    };
    use std::ffi::OsStr;
    use std::os::windows::ffi::OsStrExt;

    // Intuit writes this key for every QB Desktop install (2010+)
    let key_path: Vec<u16> = OsStr::new("SOFTWARE\\Intuit\\QuickBooks")
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();

    let mut hkey = HKEY::default();
    let result = unsafe {
        RegOpenKeyExW(
            HKEY_LOCAL_MACHINE,
            windows::core::PCWSTR(key_path.as_ptr()),
            0,
            KEY_READ,
            &mut hkey,
        )
    };

    if result.is_ok() {
        unsafe { let _ = RegCloseKey(hkey); }
        return true;
    }

    // Also check 32-bit registry hive on 64-bit Windows
    let key_path_32: Vec<u16> = OsStr::new("SOFTWARE\\WOW6432Node\\Intuit\\QuickBooks")
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();

    let result2 = unsafe {
        RegOpenKeyExW(
            HKEY_LOCAL_MACHINE,
            windows::core::PCWSTR(key_path_32.as_ptr()),
            0,
            KEY_READ,
            &mut hkey,
        )
    };

    if result2.is_ok() {
        unsafe { let _ = RegCloseKey(hkey); }
        return true;
    }

    false
}

#[cfg(target_os = "macos")]
fn detect_qb() -> bool {
    use std::path::Path;
    // Intuit installs QB to /Applications/QuickBooks <year>.app
    // Check for any matching bundle in /Applications
    if let Ok(entries) = std::fs::read_dir("/Applications") {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name_str = name.to_string_lossy();
            if name_str.starts_with("QuickBooks") && name_str.ends_with(".app") {
                return true;
            }
        }
    }
    // Also check user Applications folder
    if let Ok(home) = std::env::var("HOME") {
        let user_apps = Path::new(&home).join("Applications");
        if let Ok(entries) = std::fs::read_dir(user_apps) {
            for entry in entries.flatten() {
                let name = entry.file_name();
                let name_str = name.to_string_lossy();
                if name_str.starts_with("QuickBooks") && name_str.ends_with(".app") {
                    return true;
                }
            }
        }
    }
    false
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
fn detect_qb() -> bool {
    false
}
