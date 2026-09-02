use serde::Serialize;
use serde_json::Value;
use std::env;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::Command;

const ALLOWED_COMMANDS: &[&str] = &[
    "app",
    "bootstrap",
    "skill",
    "search",
    "source",
    "scan",
    "project",
    "agent",
    "profile",
    "llm",
];
const CORE_BINARY_NAME: &str = "adaptive-skills-core";

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

#[derive(Debug)]
enum CoreRuntime {
    Bundled(PathBuf),
    Python(OsString),
}

impl CoreRuntime {
    fn command(&self, root: &Path) -> Result<Command, CommandFailure> {
        match self {
            Self::Bundled(executable) => Ok(Command::new(executable)),
            Self::Python(executable) => {
                let mut command = Command::new(executable);
                command
                    .arg("-m")
                    .arg("adaptive_skills")
                    .current_dir(root)
                    .env("PYTHONPATH", python_path(root)?);
                Ok(command)
            }
        }
    }
}

fn packaged_core_path_for(resources: &Path, windows: bool) -> PathBuf {
    let executable = if windows {
        format!("{CORE_BINARY_NAME}.exe")
    } else {
        CORE_BINARY_NAME.into()
    };
    resources.join(CORE_BINARY_NAME).join(executable)
}

fn packaged_core_path(resources: &Path) -> PathBuf {
    packaged_core_path_for(resources, cfg!(target_os = "windows"))
}
fn development_python_path(root: &Path, windows: bool) -> PathBuf {
    if windows {
        root.join(".venv/Scripts/python.exe")
    } else {
        root.join(".venv/bin/python")
    }
}

fn development_python_command(windows: bool) -> &'static str {
    if windows {
        "python"
    } else {
        "python3"
    }
}

fn core_runtime(root: &Path, resources: Option<&Path>) -> Result<CoreRuntime, CommandFailure> {
    if let Some(configured) = env::var_os("ADAPTIVE_SKILLS_CORE") {
        return Ok(CoreRuntime::Bundled(PathBuf::from(configured)));
    }

    if let Some(resources) = resources {
        let bundled = packaged_core_path(resources);
        if bundled.is_file() {
            return Ok(CoreRuntime::Bundled(bundled));
        }
    }

    if let Some(configured) = env::var_os("ADAPTIVE_SKILLS_PYTHON") {
        return Ok(CoreRuntime::Python(configured));
    }
    let windows = cfg!(target_os = "windows");
    let development_python = development_python_path(root, windows);
    if cfg!(debug_assertions) && development_python.is_file() {
        return Ok(CoreRuntime::Python(development_python.into_os_string()));
    }
    if cfg!(debug_assertions) {
        return Ok(CoreRuntime::Python(OsString::from(
            development_python_command(windows),
        )));
    }

    Err(CommandFailure {
        message: "Adaptive Skills 核心未包含在安装包中，请重新安装完整版本。".into(),
        kind: "BundledCoreMissing".into(),
        exit_code: None,
        stderr: resources.map(|path| {
            format!(
                "Expected bundled core at {}",
                packaged_core_path(path).display()
            )
        }),
    })
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

fn validate_secret_request(args: &[String], secret: &Option<String>) -> Result<(), CommandFailure> {
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
    resources: Option<PathBuf>,
) -> Result<Value, CommandFailure> {
    validate_request(&library, &args)?;
    let root = project_root();
    let mut command = core_runtime(&root, resources.as_deref())?.command(&root)?;
    command
        .arg("--library")
        .arg(&library)
        .arg("--compact")
        .args(&args);
    if let Some(secret) = profile_secret {
        command.env("ADAPTIVE_SKILLS_LLM_PROFILE_SECRET", secret);
    }
    let output = command.output().map_err(|error| CommandFailure {
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

fn execute(
    library: String,
    args: Vec<String>,
    resources: Option<PathBuf>,
) -> Result<Value, CommandFailure> {
    execute_with_secret(library, args, None, resources)
}

#[tauri::command]
async fn run_adaptive_command(
    app: tauri::AppHandle,
    library: String,
    args: Vec<String>,
) -> Result<Value, CommandFailure> {
    use tauri::Manager;
    let resources = app.path().resource_dir().map_err(|error| {
        CommandFailure::validation(format!("Could not locate application resources: {error}"))
    })?;
    tauri::async_runtime::spawn_blocking(move || execute(library, args, Some(resources)))
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
    app: tauri::AppHandle,
    library: String,
    args: Vec<String>,
    secret: Option<String>,
) -> Result<Value, CommandFailure> {
    use tauri::Manager;
    validate_secret_request(&args, &secret)?;
    let resources = app.path().resource_dir().map_err(|error| {
        CommandFailure::validation(format!("Could not locate application resources: {error}"))
    })?;
    tauri::async_runtime::spawn_blocking(move || {
        execute_with_secret(library, args, secret, Some(resources))
    })
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
        .invoke_handler(tauri::generate_handler![
            run_adaptive_command,
            save_llm_profile
        ])
        .run(tauri::generate_context!())
        .expect("error while running Adaptive Skills desktop");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_unknown_commands_and_null_bytes() {
        assert!(validate_request("/tmp/skills", &["unknown".into()]).is_err());
        assert!(validate_request("/tmp/skills", &["inventory".into()]).is_err());
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
        assert!(validate_request("/tmp/skills", &["agent".into(), "list".into()]).is_ok());
        assert!(validate_request("/tmp/skills", &["profile".into(), "list".into()]).is_ok());
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

    #[test]
    fn runtime_paths_cover_windows_and_unix_conventions() {
        let resources = Path::new("/opt/adaptive-skills/resources");
        assert_eq!(
            packaged_core_path_for(resources, false),
            resources.join("adaptive-skills-core/adaptive-skills-core")
        );
        assert_eq!(
            packaged_core_path_for(resources, true),
            resources.join("adaptive-skills-core/adaptive-skills-core.exe")
        );

        let root = Path::new("/workspace/adaptive-skills");
        assert_eq!(
            development_python_path(root, false),
            root.join(".venv/bin/python")
        );
        assert_eq!(
            development_python_path(root, true),
            root.join(".venv/Scripts/python.exe")
        );
        assert_eq!(development_python_command(false), "python3");
        assert_eq!(development_python_command(true), "python");
    }

    #[test]
    fn bundled_core_is_resolved_inside_application_resources() {
        let path = packaged_core_path(Path::new(
            "/Applications/Adaptive Skills.app/Contents/Resources",
        ));
        let executable = if cfg!(target_os = "windows") {
            "adaptive-skills-core.exe"
        } else {
            "adaptive-skills-core"
        };
        assert_eq!(
            path,
            Path::new("/Applications/Adaptive Skills.app/Contents/Resources/adaptive-skills-core")
                .join(executable)
        );
    }

    #[test]
    fn bundled_runtime_does_not_require_python_module_arguments() {
        let bundled = CoreRuntime::Bundled(PathBuf::from("/tmp/adaptive-skills-core"));
        let command = bundled.command(Path::new("/tmp/source-tree")).unwrap();
        assert_eq!(command.get_program(), "/tmp/adaptive-skills-core");
        assert_eq!(command.get_args().count(), 0);

        let python = CoreRuntime::Python(OsString::from("python-test"));
        let command = python.command(Path::new("/tmp/source-tree")).unwrap();
        assert_eq!(
            command.get_args().collect::<Vec<_>>(),
            vec!["-m", "adaptive_skills"]
        );
    }
}
