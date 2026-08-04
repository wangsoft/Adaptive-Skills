use serde::Serialize;
use serde_json::Value;
use std::env;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::Command;

const ALLOWED_COMMANDS: &[&str] = &[
    "app",
    "skill",
    "search",
    "source",
    "scan",
    "project",
    "inventory",
    "llm",
];

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct CommandFailure {
    message: String,
    kind: String,
    exit_code: Option<i32>,
    stderr: Option<String>,
}

impl CommandFailure {
    fn validation(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            kind: "DesktopBridgeValidation".into(),
            exit_code: None,
            stderr: None,
        }
    }
}

fn project_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("src-tauri must be nested under app/")
        .to_path_buf()
}

fn python_executable(root: &Path) -> OsString {
    if let Some(configured) = env::var_os("ADAPTIVE_SKILLS_PYTHON") {
        return configured;
    }
    let development_python = root.join(".venv/bin/python");
    if development_python.is_file() {
        return development_python.into_os_string();
    }
    OsString::from("python3")
}

fn python_path(root: &Path) -> Result<OsString, CommandFailure> {
    let mut paths = vec![root.join("src")];
    if let Some(existing) = env::var_os("PYTHONPATH") {
        paths.extend(env::split_paths(&existing));
    }
    env::join_paths(paths).map_err(|error| {
        CommandFailure::validation(format!("Could not prepare Python module path: {error}"))
    })
}

fn validate_request(library: &str, args: &[String]) -> Result<(), CommandFailure> {
    if library.trim().is_empty() || library.contains('\0') {
        return Err(CommandFailure::validation(
            "A valid library path is required",
        ));
    }
    if args.is_empty() || !ALLOWED_COMMANDS.contains(&args[0].as_str()) {
        return Err(CommandFailure::validation(
            "Unsupported adaptive-skills command",
        ));
    }
    if args.len() > 128
        || args
            .iter()
            .any(|value| value.len() > 32_768 || value.contains('\0'))
    {
        return Err(CommandFailure::validation(
            "Command arguments exceed the local bridge limits",
        ));
    }
    Ok(())
}

fn validate_secret_request(
    args: &[String],
    secret: &Option<String>,
) -> Result<(), CommandFailure> {
    if args.get(0).map(String::as_str) != Some("llm")
        || args.get(1).map(String::as_str) != Some("profile")
        || args.get(2).map(String::as_str) != Some("save")
    {
        return Err(CommandFailure::validation(
            "The secret bridge only accepts an LLM profile save command",
        ));
    }
    if secret
        .as_ref()
        .is_some_and(|value| value.is_empty() || value.len() > 8192 || value.contains('\0'))
    {
        return Err(CommandFailure::validation("The API key is invalid"));
    }
    Ok(())
}

fn execute_with_secret(
    library: String,
    args: Vec<String>,
    profile_secret: Option<String>,
) -> Result<Value, CommandFailure> {
    validate_request(&library, &args)?;
    let root = project_root();
    let mut command = Command::new(python_executable(&root));
    command
        .arg("-m")
        .arg("adaptive_skills")
        .arg("--library")
        .arg(&library)
        .arg("--compact")
        .args(&args)
        .current_dir(&root)
        .env("PYTHONPATH", python_path(&root)?);
    if let Some(secret) = profile_secret {
        command.env("ADAPTIVE_SKILLS_LLM_PROFILE_SECRET", secret);
    }
    let output = command
        .output()
        .map_err(|error| CommandFailure {
            message: format!("Could not start the adaptive-skills core: {error}"),
            kind: "CoreLaunchError".into(),
            exit_code: None,
            stderr: None,
        })?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if !output.status.success() {
        let parsed = serde_json::from_str::<Value>(&stderr).ok();
        let message = parsed
            .as_ref()
            .and_then(|value| value.get("error"))
            .and_then(Value::as_str)
            .unwrap_or_else(|| {
                if stderr.is_empty() {
                    "Adaptive Skills command failed"
                } else {
                    &stderr
                }
            });
        let kind = parsed
            .as_ref()
            .and_then(|value| value.get("type"))
            .and_then(Value::as_str)
            .unwrap_or("AdaptiveSkillsError");
        return Err(CommandFailure {
            message: message.to_string(),
            kind: kind.to_string(),
            exit_code: output.status.code(),
            stderr: (!stderr.is_empty()).then_some(stderr),
        });
    }

    serde_json::from_str(stdout.trim()).map_err(|error| CommandFailure {
        message: format!("The adaptive-skills core returned invalid JSON: {error}"),
        kind: "CoreContractError".into(),
        exit_code: output.status.code(),
        stderr: (!stderr.is_empty()).then_some(stderr),
    })
}

fn execute(library: String, args: Vec<String>) -> Result<Value, CommandFailure> {
    execute_with_secret(library, args, None)
}

#[tauri::command]
async fn run_adaptive_command(library: String, args: Vec<String>) -> Result<Value, CommandFailure> {
    tauri::async_runtime::spawn_blocking(move || execute(library, args))
        .await
        .map_err(|error| CommandFailure {
            message: format!("The adaptive-skills task could not be joined: {error}"),
            kind: "DesktopBridgeError".into(),
            exit_code: None,
            stderr: None,
        })?
}

#[tauri::command]
async fn save_llm_profile(
    library: String,
    args: Vec<String>,
    secret: Option<String>,
) -> Result<Value, CommandFailure> {
    validate_secret_request(&args, &secret)?;
    tauri::async_runtime::spawn_blocking(move || execute_with_secret(library, args, secret))
        .await
        .map_err(|error| CommandFailure {
            message: format!("The adaptive-skills task could not be joined: {error}"),
            kind: "DesktopBridgeError".into(),
            exit_code: None,
            stderr: None,
        })?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![run_adaptive_command, save_llm_profile])
        .run(tauri::generate_context!())
        .expect("error while running Adaptive Skills desktop");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_unknown_commands_and_null_bytes() {
        assert!(validate_request("/tmp/skills", &["unknown".into()]).is_err());
        assert!(validate_request("/tmp/skills\0escape", &["app".into()]).is_err());
    }

    #[test]
    fn accepts_known_commands_as_separate_arguments() {
        let args = vec![
            "project".into(),
            "status".into(),
            "/tmp/project with spaces".into(),
        ];
        assert!(validate_request("/tmp/skills", &args).is_ok());
        assert!(validate_request("/tmp/skills", &["llm".into(), "status".into()]).is_ok());
    }

    #[test]
    fn secret_bridge_contract_never_requires_a_secret_argument() {
        let args = vec!["llm".into(), "profile".into(), "save".into()];
        assert!(validate_request("/tmp/skills", &args).is_ok());
        assert!(!args.iter().any(|value| value.contains("api-key")));
        assert!(validate_secret_request(&args, &Some("test-secret".into())).is_ok());

        let invalid = "sensitive\0value".to_string();
        let error = validate_secret_request(&args, &Some(invalid.clone())).unwrap_err();
        assert!(!error.message.contains(&invalid));
    }
}
