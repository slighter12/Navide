//! Launch: the argv and environment one pane starts with.
use crate::decl::Cli;
use crate::resume;

#[derive(Debug, Default, Clone)]
pub struct Request {
    /// Unattended: append the CLI's permission bypass.
    pub yolo: bool,
    pub model: Option<String>,
    pub effort: Option<String>,
    /// Session id to continue.
    pub resume: Option<String>,
}

#[derive(Debug, PartialEq, Eq)]
pub enum Refused {
    /// The CLI cannot be told its model at launch (main refuses rather than
    /// starting on the default).
    Model,
    /// The CLI has no effort flag.
    Effort,
    /// Not one of the CLI's effort levels.
    UnknownEffort(String),
    /// The CLI cannot resume by id.
    Resume,
}

fn fill(template: &'static [&'static str], value: &str) -> impl Iterator<Item = String> {
    let value = value.to_owned();
    template.iter().map(move |a| a.replace("{}", &value))
}

/// `program base… [resume…] [model…] [effort…] [yolo…]`.
pub fn argv(cli: &Cli, request: &Request) -> Result<Vec<String>, Refused> {
    let launch = &cli.launch;
    let mut argv: Vec<String> = std::iter::once(launch.program).chain(launch.base_args.iter().copied()).map(str::to_owned).collect();
    if let Some(id) = &request.resume {
        argv.extend(resume::args(cli, id).ok_or(Refused::Resume)?);
    }
    if let Some(model) = &request.model {
        argv.extend(fill(launch.model.ok_or(Refused::Model)?, model));
    }
    if let Some(effort) = &request.effort {
        let template = launch.effort.ok_or(Refused::Effort)?;
        if !launch.efforts.is_empty() && !launch.efforts.contains(&effort.as_str()) {
            return Err(Refused::UnknownEffort(effort.clone()));
        }
        argv.extend(fill(template, effort));
    }
    if request.yolo {
        argv.extend(launch.yolo_args.iter().map(|a| (*a).to_owned()));
    }
    Ok(argv)
}

/// The pane's environment: the inherited one without the names that would
/// relocate the CLI's home, the request's on top, then the declared defaults
/// for names neither set.
pub fn env(cli: &Cli, inherited: &[(String, String)], requested: &[(String, String)]) -> Vec<(String, String)> {
    let launch = &cli.launch;
    let mut out: Vec<(String, String)> =
        inherited.iter().filter(|(k, _)| !launch.env_strip.contains(&k.as_str())).cloned().collect();
    for (k, v) in requested {
        out.retain(|(name, _)| name != k);
        out.push((k.clone(), v.clone()));
    }
    for (k, v) in launch.env_defaults {
        if !out.iter().any(|(name, _)| name == k) {
            out.push(((*k).to_owned(), (*v).to_owned()));
        }
    }
    out
}
