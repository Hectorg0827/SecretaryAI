/// Cross-platform credential storage.
/// - Windows: Windows Credential Manager (DPAPI)
/// - macOS: Keychain Services
/// Credentials are NEVER stored in plain text files.

use anyhow::Result;

/// Store a credential securely in the OS credential store.
#[tauri::command]
pub fn store_credential(service: String, key: String, value: String) -> Result<(), String> {
    store_impl(&service, &key, &value).map_err(|e| e.to_string())
}

/// Retrieve a credential from the OS credential store.
#[tauri::command]
pub fn get_credential(service: String, key: String) -> Result<Option<String>, String> {
    get_impl(&service, &key).map_err(|e| e.to_string())
}

/// Delete a credential from the OS credential store.
#[tauri::command]
pub fn delete_credential(service: String, key: String) -> Result<(), String> {
    delete_impl(&service, &key).map_err(|e| e.to_string())
}

// ─── Windows implementation ──────────────────────────────────────────────────

#[cfg(target_os = "windows")]
mod platform {
    use anyhow::{anyhow, Result};
    use std::ffi::OsStr;
    use std::iter::once;
    use std::os::windows::ffi::OsStrExt;

    fn to_wide(s: &str) -> Vec<u16> {
        OsStr::new(s).encode_wide().chain(once(0)).collect()
    }

    pub fn store(service: &str, key: &str, value: &str) -> Result<()> {
        use windows::Win32::Security::Credentials::*;
        use windows::Win32::Foundation::*;

        let target = to_wide(&format!("SecretaryAI/{}/{}", service, key));
        let blob = value.as_bytes();

        let cred = CREDENTIALW {
            Type: CRED_TYPE_GENERIC,
            TargetName: windows::core::PWSTR(target.as_ptr() as *mut _),
            CredentialBlobSize: blob.len() as u32,
            CredentialBlob: blob.as_ptr() as *mut u8,
            Persist: CRED_PERSIST_LOCAL_MACHINE,
            ..Default::default()
        };

        unsafe {
            CredWriteW(&cred, 0).map_err(|e| anyhow!("CredWrite failed: {}", e))?;
        }
        Ok(())
    }

    pub fn get(service: &str, key: &str) -> Result<Option<String>> {
        use windows::Win32::Security::Credentials::*;

        let target = to_wide(&format!("SecretaryAI/{}/{}", service, key));
        let mut cred_ptr = std::ptr::null_mut();

        unsafe {
            match CredReadW(
                windows::core::PWSTR(target.as_ptr() as *mut _),
                CRED_TYPE_GENERIC,
                0,
                &mut cred_ptr,
            ) {
                Ok(_) => {
                    let cred = &*cred_ptr;
                    let blob = std::slice::from_raw_parts(
                        cred.CredentialBlob,
                        cred.CredentialBlobSize as usize,
                    );
                    let value = String::from_utf8(blob.to_vec())
                        .map_err(|e| anyhow!("UTF-8 error: {}", e))?;
                    CredFree(cred_ptr as *mut _);
                    Ok(Some(value))
                }
                Err(_) => Ok(None),
            }
        }
    }

    pub fn delete(service: &str, key: &str) -> Result<()> {
        use windows::Win32::Security::Credentials::*;

        let target = to_wide(&format!("SecretaryAI/{}/{}", service, key));
        unsafe {
            let _ = CredDeleteW(
                windows::core::PWSTR(target.as_ptr() as *mut _),
                CRED_TYPE_GENERIC,
                0,
            );
        }
        Ok(())
    }
}

// ─── macOS implementation ─────────────────────────────────────────────────────

#[cfg(target_os = "macos")]
mod platform {
    use anyhow::{anyhow, Result};
    use security_framework::passwords::{delete_generic_password, get_generic_password, set_generic_password};

    pub fn store(service: &str, key: &str, value: &str) -> Result<()> {
        set_generic_password(service, key, value.as_bytes())
            .map_err(|e| anyhow!("Keychain store error: {}", e))
    }

    pub fn get(service: &str, key: &str) -> Result<Option<String>> {
        match get_generic_password(service, key) {
            Ok(bytes) => Ok(Some(
                String::from_utf8(bytes).map_err(|e| anyhow!("UTF-8 error: {}", e))?,
            )),
            Err(_) => Ok(None),
        }
    }

    pub fn delete(service: &str, key: &str) -> Result<()> {
        let _ = delete_generic_password(service, key);
        Ok(())
    }
}

// ─── Linux fallback (encrypted file) ─────────────────────────────────────────

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
mod platform {
    use anyhow::Result;

    pub fn store(_service: &str, _key: &str, _value: &str) -> Result<()> {
        // TODO: Use libsecret / Secret Service API on Linux
        Err(anyhow::anyhow!("Linux credential store not yet implemented"))
    }

    pub fn get(_service: &str, _key: &str) -> Result<Option<String>> {
        Ok(None)
    }

    pub fn delete(_service: &str, _key: &str) -> Result<()> {
        Ok(())
    }
}

// ─── Delegating functions ─────────────────────────────────────────────────────

fn store_impl(service: &str, key: &str, value: &str) -> Result<()> {
    platform::store(service, key, value)
}

fn get_impl(service: &str, key: &str) -> Result<Option<String>> {
    platform::get(service, key)
}

fn delete_impl(service: &str, key: &str) -> Result<()> {
    platform::delete(service, key)
}
